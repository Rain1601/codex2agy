#!/usr/bin/env python3
"""Zero-dependency, cross-platform atomic JSON ledger, lock, and rollback engine for codex2agy.

Design for maximum portability (macOS / Linux / Windows, Python 3.9+ stdlib only):
- No SQLite binary DB, no third-party packages, no Unix-only `fcntl`.
- Ledger stored as human-readable JSON at:
    ~/.gemini/antigravity/plugin_data/codex2agy/migration_ledger.json
- Every write is atomic (`tmp` + `fsync` + `os.replace`) so interrupts never corrupt state.
- Exclusive lock uses atomic `O_CREAT | O_EXCL` lockfile with dead-PID stale-lock recovery.
- `synced_sessions` keyed uniquely by `codex_thread_id` guarantees a Codex session can never
  be synced twice while active/committed, even across retries or different target plugin names.
"""

import contextlib
import datetime
import json
import os
import shutil
import tempfile
import urllib.request
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

DEFAULT_LEDGER_DIR = os.path.abspath(os.path.expanduser("~/.gemini/antigravity/plugin_data/codex2agy"))
LEDGER_FILENAME = "migration_ledger.json"
LOCK_FILENAME = "migration.lock"


def utc_now() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def get_ledger_paths(ledger_dir: Optional[str] = None) -> Tuple[str, str]:
    base = os.path.abspath(os.path.expanduser(ledger_dir)) if ledger_dir else DEFAULT_LEDGER_DIR
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, LEDGER_FILENAME), os.path.join(base, LOCK_FILENAME)


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Windows fallback or invalid pid
        return False


def _try_clean_stale_lock(lock_path: str) -> bool:
    """Remove lockfile only if its recorded owner PID is definitively dead."""
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            info = json.load(f)
        pid = int(info.get("pid", -1))
    except Exception:
        return False
    if pid > 0 and not _is_pid_alive(pid):
        try:
            os.remove(lock_path)
            return True
        except OSError:
            return False
    return False


@contextlib.contextmanager
def migration_lock(ledger_dir: Optional[str] = None) -> Iterator[None]:
    """Cross-platform exclusive lock via atomic O_CREAT|O_EXCL lockfile."""
    _, lock_path = get_ledger_paths(ledger_dir)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = None
    for attempt in range(2):
        try:
            fd = os.open(lock_path, flags, 0o600)
            break
        except FileExistsError:
            if attempt == 0 and _try_clean_stale_lock(lock_path):
                continue
            raise RuntimeError(
                f"Another codex2agy migration or rollback is currently running (lock: {lock_path}). "
                "Refusing concurrent execution to protect session idempotency."
            )
    assert fd is not None
    try:
        payload = json.dumps({"pid": os.getpid(), "acquired_at": utc_now()})
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        fd = None
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            if os.path.exists(lock_path):
                os.remove(lock_path)
        except OSError:
            pass


def _empty_ledger() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "runs": {},            # run_id -> {run_id, target_plugin_name, target_plugin_dir, status, created_at, finished_at, note, actions: [...]}
        "synced_sessions": {}, # codex_thread_id -> {codex_thread_id, run_id, agy_project_id, agy_project_name, agy_conversation_id, transcript_path, status, synced_at}
    }


class JsonLedger:
    """In-memory view backed by atomic JSON file persistence."""

    def __init__(self, ledger_dir: Optional[str] = None, readonly: bool = False):
        self.readonly = readonly
        self.ledger_dir = ledger_dir
        if readonly:
            base = os.path.abspath(os.path.expanduser(ledger_dir)) if ledger_dir else DEFAULT_LEDGER_DIR
            self.path = os.path.join(base, LEDGER_FILENAME)
        else:
            self.path, _ = get_ledger_paths(ledger_dir)
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            return _empty_ledger()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                return _empty_ledger()
            loaded.setdefault("schema_version", 1)
            loaded.setdefault("runs", {})
            loaded.setdefault("synced_sessions", {})
            return loaded
        except Exception:
            return _empty_ledger()

    def save(self) -> None:
        if self.readonly:
            raise RuntimeError("Cannot save a read-only ledger")
        parent = os.path.dirname(self.path)
        os.makedirs(parent, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".ledger.", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise

    def start_run(self, run_id: str, target_plugin_name: str, target_plugin_dir: str) -> None:
        self.data["runs"][run_id] = {
            "run_id": run_id,
            "target_plugin_name": target_plugin_name,
            "target_plugin_dir": target_plugin_dir,
            "status": "running",
            "created_at": utc_now(),
            "finished_at": None,
            "note": None,
            "actions": [],
        }
        self.save()

    def append_action(self, run_id: str, action: Dict[str, Any]) -> None:
        run = self.data["runs"][run_id]
        action["seq"] = len(run["actions"]) + 1
        action["created_at"] = utc_now()
        run["actions"].append(action)
        self.save()

    def reserve_session(
        self,
        codex_thread_id: str,
        run_id: str,
        agy_project_id: str,
        agy_project_name: str,
        transcript_path: str,
    ) -> bool:
        """Atomically reserve a session ID. Returns False if already synced/syncing."""
        if codex_thread_id in self.data["synced_sessions"]:
            return False
        self.data["synced_sessions"][codex_thread_id] = {
            "codex_thread_id": codex_thread_id,
            "run_id": run_id,
            "agy_project_id": agy_project_id,
            "agy_project_name": agy_project_name,
            "agy_conversation_id": None,
            "transcript_path": transcript_path,
            "status": "syncing",
            "synced_at": utc_now(),
        }
        self.save()
        return True

    def mark_session_synced(self, codex_thread_id: str, conv_id: Optional[str]) -> None:
        entry = self.data["synced_sessions"].get(codex_thread_id)
        if entry is not None:
            entry["agy_conversation_id"] = conv_id
            entry["status"] = "synced"
            entry["synced_at"] = utc_now()
            self.save()

    def finish_run(self, run_id: str, status: str, note: Optional[str] = None) -> None:
        run = self.data["runs"].get(run_id)
        if run is not None:
            run["status"] = status
            run["finished_at"] = utc_now()
            run["note"] = note
            self.save()


def call_conversation_service(method: str, payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    addr = os.environ.get("ANTIGRAVITY_LS_ADDRESS")
    csrf = os.environ.get("ANTIGRAVITY_CSRF_TOKEN")
    if not addr or not csrf:
        return None, "ANTIGRAVITY_LS_ADDRESS or ANTIGRAVITY_CSRF_TOKEN not set in environment"
    url = f"http://{addr}/product.v1.ConversationService/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "x-antigravity-csrf-token": csrf,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except Exception as exc:
        return None, str(exc)


def get_synced_session_ids(ledger_dir: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    return dict(JsonLedger(ledger_dir, readonly=True).data.get("synced_sessions") or {})


def get_incomplete_runs(ledger_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    runs = (JsonLedger(ledger_dir, readonly=True).data.get("runs") or {}).values()
    out = [
        {
            "run_id": r["run_id"],
            "target_plugin_name": r["target_plugin_name"],
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in runs
        if r.get("status") == "running"
    ]
    return sorted(out, key=lambda x: x.get("created_at") or "", reverse=True)


def list_runs(ledger_dir: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    data = JsonLedger(ledger_dir, readonly=True).data
    synced = data.get("synced_sessions") or {}
    out = []
    for r in (data.get("runs") or {}).values():
        rid = r["run_id"]
        out.append({
            "run_id": rid,
            "target_plugin_name": r.get("target_plugin_name"),
            "target_plugin_dir": r.get("target_plugin_dir"),
            "status": r.get("status"),
            "created_at": r.get("created_at"),
            "finished_at": r.get("finished_at"),
            "note": r.get("note"),
            "action_count": len(r.get("actions") or []),
            "synced_session_count": sum(1 for s in synced.values() if s.get("run_id") == rid),
        })
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out[:limit]


def _remove_empty_parents(path: str, stop_at: str) -> None:
    stop_abs = os.path.abspath(stop_at)
    cur = os.path.abspath(path)
    while cur != stop_abs and cur.startswith(stop_abs + os.sep):
        try:
            if os.path.isdir(cur) and not os.listdir(cur):
                os.rmdir(cur)
                cur = os.path.dirname(cur)
            else:
                break
        except OSError:
            break


def rollback_run(
    ledger: JsonLedger,
    run_id: str,
    final_status: str = "rolled_back",
    reason: str = "manual_rollback",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Reverse every action recorded for `run_id` in strict reverse order."""
    run_row = (ledger.data.get("runs") or {}).get(run_id)
    if not run_row:
        return {"error": f"Run not found: {run_id}"}

    if run_row.get("status") in ("rolled_back", "interrupted_rolled_back", "failed_rolled_back"):
        return {
            "run_id": run_id,
            "status": run_row["status"],
            "already_rolled_back": True,
            "reverted": [],
            "errors": [],
        }

    actions = list(reversed(run_row.get("actions") or []))
    target_plugin_dir = run_row["target_plugin_dir"]
    reverted: List[Dict[str, Any]] = []
    errors: List[str] = []

    for act in actions:
        kind = act.get("kind")
        tpath = act.get("target_path")
        conv_id = act.get("conversation_id")
        mcp_name = act.get("mcp_server_name")
        tid = act.get("codex_thread_id")

        if kind == "project_conversation":
            if conv_id:
                if not dry_run:
                    _, err = call_conversation_service("DeleteConversation", {"conversationId": conv_id})
                    if err:
                        errors.append(f"DeleteConversation({conv_id}) failed: {err}")
                    else:
                        reverted.append({"kind": kind, "conversation_id": conv_id, "codex_thread_id": tid})
                else:
                    reverted.append({"kind": kind, "conversation_id": conv_id, "codex_thread_id": tid, "dry_run": True})
            continue

        if kind == "mcp_entry" and tpath and mcp_name:
            if os.path.isfile(tpath):
                try:
                    with open(tpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    servers = data.get("mcpServers") if isinstance(data, dict) else None
                    if isinstance(servers, dict) and mcp_name in servers:
                        if not dry_run:
                            del servers[mcp_name]
                            with open(tpath, "w", encoding="utf-8") as f:
                                json.dump(data, f, ensure_ascii=False, indent=2)
                                f.write("\n")
                        reverted.append({"kind": kind, "mcp_server_name": mcp_name, "path": tpath})
                except Exception as exc:
                    errors.append(f"Revert mcp_entry {mcp_name} in {tpath} failed: {exc}")
            continue

        if kind == "mcp_file" and tpath:
            if os.path.isfile(tpath):
                try:
                    with open(tpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    servers = data.get("mcpServers") if isinstance(data, dict) else {}
                    if not servers:
                        if not dry_run:
                            os.remove(tpath)
                        reverted.append({"kind": kind, "path": tpath})
                except Exception as exc:
                    errors.append(f"Remove newly created mcp_config.json {tpath} failed: {exc}")
            continue

        if kind in ("skill", "prompt_skill", "memory_lookup_skill") and tpath:
            if os.path.isdir(tpath):
                try:
                    if not dry_run:
                        shutil.rmtree(tpath)
                        _remove_empty_parents(os.path.dirname(tpath), target_plugin_dir)
                    reverted.append({"kind": kind, "name": act.get("item_name"), "path": tpath})
                except Exception as exc:
                    errors.append(f"Remove skill dir {tpath} failed: {exc}")
            continue

        if kind in ("rule", "memory_rule", "project_transcript", "plugin_manifest") and tpath:
            if os.path.isfile(tpath):
                try:
                    if not dry_run:
                        os.remove(tpath)
                        _remove_empty_parents(os.path.dirname(tpath), target_plugin_dir)
                    reverted.append({"kind": kind, "name": act.get("item_name"), "path": tpath})
                except Exception as exc:
                    errors.append(f"Remove file {tpath} failed: {exc}")
            continue

        if kind == "plugin_dir" and tpath:
            if os.path.isdir(tpath):
                try:
                    if not dry_run and not os.listdir(tpath):
                        os.rmdir(tpath)
                        reverted.append({"kind": kind, "path": tpath})
                    elif dry_run:
                        reverted.append({"kind": kind, "path": tpath, "note": "will remove if empty"})
                except Exception as exc:
                    errors.append(f"Remove empty plugin dir {tpath} failed: {exc}")

    if not dry_run:
        failed_conv_ids: Set[str] = set()
        for err in errors:
            if err.startswith("DeleteConversation("):
                cid = err.split("(", 1)[1].split(")", 1)[0]
                failed_conv_ids.add(cid)

        synced = ledger.data.get("synced_sessions") or {}
        to_delete = [
            tid
            for tid, s in synced.items()
            if s.get("run_id") == run_id and (not s.get("agy_conversation_id") or s.get("agy_conversation_id") not in failed_conv_ids)
        ]
        for tid in to_delete:
            del synced[tid]

        ledger.finish_run(run_id, final_status, reason if not errors else f"{reason}; errors={len(errors)}")

    return {
        "run_id": run_id,
        "dry_run": dry_run,
        "status": final_status if not dry_run else run_row.get("status"),
        "reason": reason,
        "reverted_count": len(reverted),
        "reverted": reverted,
        "errors": errors,
    }
