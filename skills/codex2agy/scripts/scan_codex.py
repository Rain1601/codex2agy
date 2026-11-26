#!/usr/bin/env python3
"""Read-only scanner for migrating Codex settings/assets & matching Projects into Antigravity.

Python 3.9+ compatible (no third-party packages required).
Never modifies any file and never prints secret values (tokens/keys/env values).
"""

import argparse
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

try:
    import sqlite3
except ImportError:
    sqlite3 = None  # type: ignore

from ledger import get_incomplete_runs, get_synced_session_ids


@contextlib.contextmanager
def open_codex_sqlite_readonly(db_path: str) -> Iterator[Optional[Any]]:
    """Open a Codex SQLite DB safely even while Codex holds WAL locks.

    Tries direct read-only open first; falls back to copying db + wal/shm into a
    short-lived temp directory so live Codex processes are never blocked or disturbed.
    """
    if sqlite3 is None or not os.path.isfile(db_path):
        yield None
        return

    tmp_dir: Optional[str] = None
    con = None
    try:
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
            con.execute("SELECT 1").fetchone()
        except Exception:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass
                con = None
            tmp_dir = tempfile.mkdtemp(prefix="codex2agy_ro_")
            dst = os.path.join(tmp_dir, os.path.basename(db_path))
            shutil.copy2(db_path, dst)
            for suf in ("-wal", "-shm"):
                src_side = db_path + suf
                if os.path.isfile(src_side):
                    try:
                        shutil.copy2(src_side, dst + suf)
                    except OSError:
                        pass
            con = sqlite3.connect(dst, timeout=3)
        yield con
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

RULE_BYTE_LIMIT = 24000
BUILTIN_CODEX_MARKETPLACES = {
    "openai-bundled",
    "openai-primary-runtime",
    "openai-curated-remote",
}
INTERNAL_AGY_PROJECT_NAMES = {"swarm/swarm_board"}


def expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def norm_path(path: str) -> str:
    return os.path.normpath(os.path.realpath(os.path.expanduser(path))) if path else ""


def safe_read_text(path: str, max_bytes: int = 65536) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(max_bytes)
    except OSError:
        return ""


def parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text
    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :])
    meta: Dict[str, str] = {}
    cur_key: Optional[str] = None
    buf: List[str] = []

    def flush() -> None:
        nonlocal cur_key, buf
        if cur_key is not None:
            meta[cur_key] = " ".join(x.strip() for x in buf if x.strip()).strip().strip('"').strip("'")
            cur_key = None
            buf = []

    for line in fm_lines:
        m = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if m:
            flush()
            key, val = m.group(1), m.group(2).strip()
            if val in (">", ">-", "|", "|-", ""):
                cur_key = key
                buf = []
            else:
                meta[key] = val.strip('"').strip("'")
        elif cur_key is not None and (line.startswith(" ") or line.startswith("\t")):
            buf.append(line.strip())
    flush()
    return meta, body


def summarize_markdown(text: str, max_chars: int = 180) -> str:
    _, body = parse_frontmatter(text)
    pieces: List[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("<!--") or s.startswith("```"):
            continue
        s = re.sub(r"^#+\s*", "", s)
        pieces.append(s)
        if sum(len(p) for p in pieces) >= max_chars:
            break
    summary = " / ".join(pieces)
    return summary[:max_chars] + ("..." if len(summary) > max_chars else "")


def extract_headings(text: str, limit: int = 8) -> List[str]:
    _, body = parse_frontmatter(text)
    headings = []
    for line in body.splitlines():
        m = re.match(r"^#{1,2}\s+(.+)$", line.strip())
        if m:
            headings.append(m.group(1).strip())
            if len(headings) >= limit:
                break
    return headings


def call_conversation_service(method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    addr = os.environ.get("ANTIGRAVITY_LS_ADDRESS")
    csrf = os.environ.get("ANTIGRAVITY_CSRF_TOKEN")
    if not addr or not csrf:
        return None
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
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def list_imported_codex_thread_ids_by_project(project_id: str) -> Set[str]:
    """Query Antigravity ConversationService for already-imported codex-thread:<id> tags."""
    imported: Set[str] = set()
    page_token: Optional[str] = None
    for _ in range(10):
        body: Dict[str, Any] = {
            "projectId": project_id,
            "pageSize": 100,
            "includeArchived": True,
            "tags": ["codex-imported"],
        }
        if page_token:
            body["pageToken"] = page_token
        resp = call_conversation_service("ListConversations", body)
        if not resp:
            break
        for conv in resp.get("conversations") or []:
            meta_tags = (conv.get("metadata") or {}).get("tags") or []
            ann_tags = (conv.get("annotations") or {}).get("tags") or []
            for t in list(meta_tags) + list(ann_tags):
                if isinstance(t, str) and t.startswith("codex-thread:"):
                    imported.add(t[len("codex-thread:") :])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return imported


def discover_existing_agy_items(gemini_config: str, agy_builtin: str, target_plugin: str) -> Dict[str, Any]:
    existing_skills: Dict[str, str] = {}
    existing_mcps: Dict[str, str] = {}
    existing_target_rules: Set[str] = set()
    existing_plugins: List[str] = []

    global_skills_dir = os.path.join(gemini_config, "skills")
    if os.path.isdir(global_skills_dir):
        for entry in sorted(os.listdir(global_skills_dir)):
            p = os.path.join(global_skills_dir, entry)
            if os.path.isdir(p) and os.path.exists(os.path.join(p, "SKILL.md")):
                existing_skills[entry] = p

    builtin_skills_dir = os.path.join(agy_builtin, "skills")
    if os.path.isdir(builtin_skills_dir):
        for entry in sorted(os.listdir(builtin_skills_dir)):
            p = os.path.join(builtin_skills_dir, entry)
            if os.path.isdir(p) and os.path.exists(os.path.join(p, "SKILL.md")):
                existing_skills.setdefault(entry, p)

    global_mcp_path = os.path.join(gemini_config, "mcp_config.json")
    if os.path.isfile(global_mcp_path) and os.path.getsize(global_mcp_path) > 0:
        try:
            with open(global_mcp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for name in (data.get("mcpServers") or {}).keys():
                existing_mcps[name] = global_mcp_path
        except Exception:
            pass

    plugins_dir = os.path.join(gemini_config, "plugins")
    if os.path.isdir(plugins_dir):
        for pdir in sorted(os.listdir(plugins_dir)):
            full = os.path.join(plugins_dir, pdir)
            if not os.path.isdir(full):
                continue
            existing_plugins.append(pdir)
            pskills = os.path.join(full, "skills")
            if os.path.isdir(pskills):
                for s in sorted(os.listdir(pskills)):
                    sp = os.path.join(pskills, s)
                    if os.path.isdir(sp) and os.path.exists(os.path.join(sp, "SKILL.md")):
                        existing_skills.setdefault(s, sp)
            pmcp = os.path.join(full, "mcp_config.json")
            if os.path.isfile(pmcp) and os.path.getsize(pmcp) > 0:
                try:
                    with open(pmcp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for name in (data.get("mcpServers") or {}).keys():
                        existing_mcps.setdefault(name, pmcp)
                except Exception:
                    pass
            if pdir == target_plugin:
                prules = os.path.join(full, "rules")
                if os.path.isdir(prules):
                    for r in os.listdir(prules):
                        if r.endswith(".md"):
                            existing_target_rules.add(r)

    return {
        "skills": existing_skills,
        "mcps": existing_mcps,
        "target_rules": existing_target_rules,
        "plugins": existing_plugins,
    }


def scan_prompts(codex_home: str, existing: Dict[str, Any]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    skipped_duplicates: List[Dict[str, Any]] = []

    agents_md = os.path.join(codex_home, "AGENTS.md")
    if os.path.isfile(agents_md):
        size = os.path.getsize(agents_md)
        text = safe_read_text(agents_md)
        target_rule_name = "codex-agents.md"
        entry = {
            "id": "codex-agents-md",
            "kind": "instruction_rule",
            "name": "AGENTS.md",
            "target_name": target_rule_name,
            "source_path": agents_md,
            "size_bytes": size,
            "exceeds_24kb_limit": size > RULE_BYTE_LIMIT,
            "headings": extract_headings(text),
            "preview": summarize_markdown(text, 220),
            "default_selected": False,
        }
        if target_rule_name in existing["target_rules"]:
            entry["reason"] = "already_exists_in_target_plugin"
            skipped_duplicates.append(entry)
        else:
            items.append(entry)

    prompts_dir = os.path.join(codex_home, "prompts")
    if os.path.isdir(prompts_dir):
        for fn in sorted(os.listdir(prompts_dir)):
            if not fn.endswith(".md"):
                continue
            fp = os.path.join(prompts_dir, fn)
            if not os.path.isfile(fp):
                continue
            stem = os.path.splitext(fn)[0]
            skill_name = re.sub(r"[^a-z0-9-]+", "-", stem.lower()).strip("-") or "codex-prompt"
            text = safe_read_text(fp)
            meta, _ = parse_frontmatter(text)
            entry = {
                "id": f"prompt:{skill_name}",
                "kind": "prompt_to_skill",
                "name": fn,
                "target_name": skill_name,
                "source_path": fp,
                "size_bytes": os.path.getsize(fp),
                "description": meta.get("description") or summarize_markdown(text, 160),
                "default_selected": False,
            }
            if skill_name in existing["skills"]:
                entry["reason"] = f"already_exists_in_antigravity ({existing['skills'][skill_name]})"
                skipped_duplicates.append(entry)
            else:
                items.append(entry)

    return {
        "default_selected": False,
        "migratable": items,
        "skipped_duplicates": skipped_duplicates,
    }


def inspect_skill_dir(skill_dir: str, source_group: str, fallback_name: Optional[str] = None) -> Dict[str, Any]:
    skill_md = os.path.join(skill_dir, "SKILL.md")
    text = safe_read_text(skill_md)
    meta, _ = parse_frontmatter(text)
    folder_name = fallback_name or os.path.basename(skill_dir)
    desc = meta.get("description") or summarize_markdown(text, 160)
    return {
        "name": folder_name,
        "frontmatter_name": meta.get("name"),
        "source_group": source_group,
        "source_path": skill_dir,
        "description": desc,
        "has_frontmatter": bool(meta),
        "has_scripts": os.path.isdir(os.path.join(skill_dir, "scripts")),
        "has_references": os.path.isdir(os.path.join(skill_dir, "references")),
        "default_selected": True,
    }


def scan_skills(codex_home: str, agents_home: str, existing: Dict[str, Any]) -> Dict[str, Any]:
    migratable: List[Dict[str, Any]] = []
    skipped_duplicates: List[Dict[str, Any]] = []
    skipped_system: List[str] = []
    seen_names: Set[str] = set()

    roots = [
        ("codex-user-skills", os.path.join(codex_home, "skills")),
        ("agents-shared-skills", os.path.join(agents_home, "skills")),
        ("codex-memory-skills", os.path.join(codex_home, "memories", "skills")),
    ]

    for group, root in roots:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            full = os.path.join(root, entry)
            if group == "codex-user-skills" and entry == ".system":
                if os.path.isdir(full):
                    for s in sorted(os.listdir(full)):
                        if os.path.exists(os.path.join(full, s, "SKILL.md")):
                            skipped_system.append(f".system/{s}")
                continue
            if not os.path.isdir(full) or not os.path.exists(os.path.join(full, "SKILL.md")):
                continue
            info = inspect_skill_dir(full, group, fallback_name=entry)
            name = info["name"]
            if name in existing["skills"]:
                skipped_duplicates.append({
                    "name": name,
                    "source_group": group,
                    "source_path": full,
                    "existing_path": existing["skills"][name],
                    "reason": "already_exists_in_antigravity",
                })
                continue
            if name in seen_names:
                skipped_duplicates.append({
                    "name": name,
                    "source_group": group,
                    "source_path": full,
                    "reason": "duplicate_across_codex_sources",
                })
                continue
            seen_names.add(name)
            migratable.append(info)

    return {
        "default_selected": True,
        "migratable": migratable,
        "skipped_duplicates": skipped_duplicates,
        "skipped_system": skipped_system,
    }


def scan_memory(codex_home: str, agy_app_dir: str, existing: Dict[str, Any]) -> Dict[str, Any]:
    summary_candidates: List[Dict[str, Any]] = []
    large_files: List[Dict[str, Any]] = []

    for subdir, rule_name in [
        ("memories", "codex-memory-summary.md"),
        ("memories_v2", "codex-memory-v2-summary.md"),
    ]:
        base = os.path.join(codex_home, subdir)
        if not os.path.isdir(base):
            continue
        sum_path = os.path.join(base, "memory_summary.md")
        if os.path.isfile(sum_path):
            size = os.path.getsize(sum_path)
            text = safe_read_text(sum_path, 4096)
            summary_candidates.append({
                "id": f"memory:{subdir}",
                "source_path": sum_path,
                "target_rule_name": rule_name,
                "size_bytes": size,
                "fits_24kb_rule_limit": size <= RULE_BYTE_LIMIT,
                "already_exists": rule_name in existing["target_rules"],
                "preview": summarize_markdown(text, 180),
            })
        for big in ("MEMORY.md", "raw_memories.md"):
            bp = os.path.join(base, big)
            if os.path.isfile(bp):
                large_files.append({
                    "source_path": bp,
                    "size_bytes": os.path.getsize(bp),
                    "fits_24kb_rule_limit": os.path.getsize(bp) <= RULE_BYTE_LIMIT,
                })
        rollouts = os.path.join(base, "rollout_summaries")
        if os.path.isdir(rollouts):
            cnt = len([x for x in os.listdir(rollouts) if not x.startswith(".")])
            large_files.append({
                "source_path": rollouts,
                "kind": "directory",
                "file_count": cnt,
                "fits_24kb_rule_limit": False,
            })

    agy_storage_observed = []
    for name in ("conversations", "conversation_summaries.db", "brain"):
        if os.path.exists(os.path.join(agy_app_dir, name)):
            agy_storage_observed.append(name)

    return {
        "codex_supported": bool(summary_candidates or large_files),
        "antigravity_native_memory_import_supported": False,
        "antigravity_storage_observed": agy_storage_observed,
        "antigravity_rule_limit_bytes": RULE_BYTE_LIMIT,
        "antigravity_rules_budget_tokens": 20000,
        "summary_candidates": summary_candidates,
        "large_files": large_files,
        "lookup_skill_exists": "codex-memory-lookup" in existing["skills"],
        "recommended_strategies": [
            "skip",
            "migrate_summaries_as_rules",
            "create_readonly_lookup_skill",
            "both_summaries_and_lookup_skill",
        ],
    }


def parse_codex_config_mcps(config_toml_path: str) -> List[Dict[str, Any]]:
    if not os.path.isfile(config_toml_path):
        return []
    text = safe_read_text(config_toml_path, max_bytes=512 * 1024)
    servers: Dict[str, Dict[str, Any]] = {}
    cur_srv: Optional[str] = None
    cur_sub: str = ""

    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^\[+([^\]]+)\]+$", s)
        if m:
            sec = m.group(1).strip()
            if sec.startswith("mcp_servers."):
                parts = [p.strip().strip('"').strip("'") for p in sec.split(".")]
                cur_srv = parts[1]
                cur_sub = ".".join(parts[2:]) if len(parts) > 2 else ""
                servers.setdefault(cur_srv, {
                    "name": cur_srv,
                    "source": config_toml_path,
                    "enabled": True,
                    "command": None,
                    "url": None,
                    "cwd": None,
                    "type": None,
                    "env_keys": [],
                    "http_header_keys": [],
                    "env_http_headers": {},
                    "bearer_token_env_var": None,
                })
            else:
                cur_srv = None
                cur_sub = ""
            continue
        if cur_srv and "=" in s:
            k, v = [x.strip() for x in s.split("=", 1)]
            k_clean = k.strip('"').strip("'")
            v_clean = v.split("#", 1)[0].strip()
            d = servers[cur_srv]
            if cur_sub == "env":
                d["env_keys"].append(k_clean)
            elif cur_sub == "http_headers":
                d["http_header_keys"].append(k_clean)
            elif cur_sub == "env_http_headers":
                d["env_http_headers"][k_clean] = v_clean.strip('"').strip("'")
            elif not cur_sub:
                if k_clean == "enabled":
                    d["enabled"] = v_clean.lower() == "true"
                elif k_clean in ("command", "url", "cwd", "type", "bearer_token_env_var"):
                    d[k_clean] = v_clean.strip('"').strip("'")
    return list(servers.values())


def pick_latest_version_dir(plugin_dir: str) -> str:
    entries = [e for e in sorted(os.listdir(plugin_dir)) if os.path.isdir(os.path.join(plugin_dir, e)) and not e.startswith(".")]
    version_dirs = [e for e in entries if re.match(r"^\d+(\.\d+)*", e)]
    if not version_dirs:
        return plugin_dir

    def ver_key(v: str) -> Tuple[int, ...]:
        nums = re.findall(r"\d+", v)
        return tuple(int(n) for n in nums) if nums else (0,)

    return os.path.join(plugin_dir, max(version_dirs, key=ver_key))


def scan_third_party_plugins_and_mcps(
    codex_home: str,
    existing: Dict[str, Any],
    already_seen_skills: Set[str],
) -> Dict[str, Any]:
    plugin_skills_migratable: List[Dict[str, Any]] = []
    plugin_skills_duplicates: List[Dict[str, Any]] = []
    mcps_migratable: List[Dict[str, Any]] = []
    mcps_duplicates: List[Dict[str, Any]] = []
    skipped_unsupported: List[Dict[str, Any]] = []

    seen_skill_names = set(already_seen_skills)
    seen_mcp_names: Set[str] = set()

    for srv in parse_codex_config_mcps(os.path.join(codex_home, "config.toml")):
        name = srv["name"]
        cmd = srv.get("command") or ""
        url = srv.get("url") or ""
        if name == "node_repl" or "ChatGPT.app" in cmd or "cua_node" in cmd:
            skipped_unsupported.append({
                "kind": "mcp",
                "name": name,
                "reason": "codex_app_internal_runtime",
            })
            continue
        if not srv.get("enabled", True):
            skipped_unsupported.append({
                "kind": "mcp",
                "name": name,
                "reason": "disabled_in_codex",
            })
            continue
        if srv.get("type") == "sse" or url.endswith("/sse"):
            skipped_unsupported.append({
                "kind": "mcp",
                "name": name,
                "reason": "legacy_sse_transport_requires_mcp_remote",
            })
            continue
        if name in existing["mcps"]:
            mcps_duplicates.append({
                "name": name,
                "source": srv["source"],
                "existing_path": existing["mcps"][name],
                "reason": "already_exists_in_antigravity",
            })
            continue
        seen_mcp_names.add(name)
        mcps_migratable.append({
            "name": name,
            "source_kind": "codex_config_toml",
            "source_path": srv["source"],
            "transport": "url" if url else "stdio",
            "command": cmd or None,
            "url": url or None,
            "env_keys": srv["env_keys"],
            "http_header_keys": srv["http_header_keys"],
            "env_http_headers": srv["env_http_headers"],
            "bearer_token_env_var": srv["bearer_token_env_var"],
            "compatible": True,
            "default_selected": True,
        })

    cache_dir = os.path.join(codex_home, "plugins", "cache")
    if os.path.isdir(cache_dir):
        for marketplace in sorted(os.listdir(cache_dir)):
            mpath = os.path.join(cache_dir, marketplace)
            if not os.path.isdir(mpath) or marketplace.startswith("."):
                continue
            if marketplace in BUILTIN_CODEX_MARKETPLACES:
                skipped_unsupported.append({
                    "kind": "plugin_marketplace",
                    "name": marketplace,
                    "reason": "codex_builtin_or_curated_runtime_plugin",
                })
                continue

            for plug in sorted(os.listdir(mpath)):
                if plug.startswith("."):
                    continue
                ppath = os.path.join(mpath, plug)
                if not os.path.isdir(ppath):
                    continue
                ver_root = pick_latest_version_dir(ppath)

                skills_dir = os.path.join(ver_root, "skills")
                if os.path.isdir(skills_dir):
                    for sname in sorted(os.listdir(skills_dir)):
                        sdir = os.path.join(skills_dir, sname)
                        if not os.path.isdir(sdir) or not os.path.exists(os.path.join(sdir, "SKILL.md")):
                            continue
                        info = inspect_skill_dir(sdir, f"plugin:{marketplace}/{plug}", fallback_name=sname)
                        info["plugin_source"] = f"{marketplace}/{plug}"
                        if sname in existing["skills"]:
                            plugin_skills_duplicates.append({
                                "name": sname,
                                "plugin_source": f"{marketplace}/{plug}",
                                "source_path": sdir,
                                "existing_path": existing["skills"][sname],
                                "reason": "already_exists_in_antigravity",
                            })
                            continue
                        if sname in seen_skill_names:
                            plugin_skills_duplicates.append({
                                "name": sname,
                                "plugin_source": f"{marketplace}/{plug}",
                                "source_path": sdir,
                                "reason": "duplicate_across_sources",
                            })
                            continue
                        seen_skill_names.add(sname)
                        plugin_skills_migratable.append(info)

                for mcp_fn in ("mcp.json", ".mcp.json"):
                    mcp_path = os.path.join(ver_root, mcp_fn)
                    if not os.path.isfile(mcp_path):
                        continue
                    try:
                        with open(mcp_path, "r", encoding="utf-8") as f:
                            mdata = json.load(f)
                    except Exception:
                        continue
                    srv_map = mdata.get("mcpServers", mdata) if isinstance(mdata, dict) else {}
                    if not isinstance(srv_map, dict):
                        continue
                    for sname, cfg in srv_map.items():
                        if not isinstance(cfg, dict):
                            continue
                        url = cfg.get("url") or ""
                        if cfg.get("type") == "sse" or (isinstance(url, str) and url.endswith("/sse")):
                            skipped_unsupported.append({
                                "kind": "plugin_mcp",
                                "name": sname,
                                "plugin_source": f"{marketplace}/{plug}",
                                "reason": "legacy_sse_transport_requires_mcp_remote",
                            })
                            continue
                        if sname in existing["mcps"]:
                            mcps_duplicates.append({
                                "name": sname,
                                "plugin_source": f"{marketplace}/{plug}",
                                "source_path": mcp_path,
                                "existing_path": existing["mcps"][sname],
                                "reason": "already_exists_in_antigravity",
                            })
                            continue
                        if sname in seen_mcp_names:
                            continue
                        seen_mcp_names.add(sname)
                        env_keys = list((cfg.get("env") or {}).keys()) if isinstance(cfg.get("env"), dict) else []
                        header_keys = list((cfg.get("headers") or {}).keys()) if isinstance(cfg.get("headers"), dict) else []
                        mcps_migratable.append({
                            "name": sname,
                            "plugin_source": f"{marketplace}/{plug}",
                            "source_kind": "codex_plugin_mcp_json",
                            "source_path": mcp_path,
                            "plugin_root": ver_root,
                            "transport": "url" if url else "stdio",
                            "command": cfg.get("command"),
                            "args": cfg.get("args"),
                            "url": url or None,
                            "env_keys": env_keys,
                            "header_keys": header_keys,
                            "compatible": True,
                            "default_selected": True,
                        })

    return {
        "default_selected": True,
        "plugin_skills_migratable": plugin_skills_migratable,
        "plugin_skills_duplicates": plugin_skills_duplicates,
        "mcps_migratable": mcps_migratable,
        "mcps_duplicates": mcps_duplicates,
        "skipped_unsupported": skipped_unsupported,
    }


def load_agy_projects(gemini_config: str) -> List[Dict[str, Any]]:
    pdir = os.path.join(gemini_config, "projects")
    projects: List[Dict[str, Any]] = []
    if not os.path.isdir(pdir):
        return projects
    for fn in sorted(os.listdir(pdir)):
        if not fn.endswith(".json"):
            continue
        fp = os.path.join(pdir, fn)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        name = data.get("name") or ""
        if not name or name in INTERNAL_AGY_PROJECT_NAMES:
            continue
        roots: List[str] = []
        for res in (data.get("projectResources") or {}).get("resources") or []:
            uri = res.get("folderUri") or (res.get("gitFolder") or {}).get("folderUri")
            if isinstance(uri, str) and uri.startswith("file://"):
                roots.append(uri[len("file://") :])
        projects.append({
            "id": data.get("id") or os.path.splitext(fn)[0],
            "name": name,
            "roots": roots,
            "norm_roots": [norm_path(r) for r in roots if r],
        })
    return projects


def detect_git_default_branch(repo_dir: str) -> Optional[str]:
    git_dir = os.path.join(repo_dir, ".git")
    if not os.path.exists(git_dir):
        return None
    head_path = os.path.join(git_dir, "HEAD") if os.path.isdir(git_dir) else None
    if head_path and os.path.isfile(head_path):
        txt = safe_read_text(head_path, 256).strip()
        if txt.startswith("ref: refs/heads/"):
            return txt[len("ref: refs/heads/") :].strip() or "main"
    return "main"


def is_virtual_or_scratch_root(path: str) -> bool:
    low = path.replace("\\", "/").lower()
    return (
        "/.codex/.chatgpt-projects/" in low
        or "/scratch-workspaces/" in low
        or "/.codex/worktrees/" in low
        or "/tmp/" in low
    )


def scan_matching_projects(
    codex_home: str,
    gemini_config: str,
    target_plugin: str,
    ledger_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Path-first project matching between Codex and Antigravity:

    1. `existing_same_path` (default_selected=True):
       Codex project shares the exact same local directory path as an existing Antigravity project.
       -> Sync its sessions directly into that existing Antigravity project.
    2. `new_codex_projects` (default_selected=True):
       Codex project has a real local directory on disk that does NOT exist in Antigravity yet.
       -> Create the corresponding Antigravity project automatically and sync its sessions.
    3. `virtual_or_scratch_projects` (default_selected=False):
       Codex projects pointing to virtual `.chatgpt-projects/` or temporary scratch folders.

    Active threads default_selected=True; archived threads default_selected=False (optional).
    """
    agy_projects = load_agy_projects(gemini_config)
    ledger_synced = get_synced_session_ids(ledger_dir)
    state_db = os.path.join(codex_home, "state_5.sqlite")
    if sqlite3 is None or not os.path.isfile(state_db):
        return {
            "existing_same_path": [],
            "new_codex_projects": [],
            "virtual_or_scratch_projects": [],
            "matched_projects": [],
        }

    codex_projects: List[Dict[str, Any]] = []
    thread_rows: List[Tuple[Any, ...]] = []
    try:
        with open_codex_sqlite_readonly(state_db) as con:
            if con is None:
                return {
                    "existing_same_path": [],
                    "new_codex_projects": [],
                    "virtual_or_scratch_projects": [],
                    "matched_projects": [],
                }
            prows = con.execute("SELECT id, name FROM projects ORDER BY position ASC").fetchall()
            root_map: Dict[str, List[str]] = {}
            for pid, path in con.execute("SELECT project_id, path FROM project_roots ORDER BY position ASC").fetchall():
                root_map.setdefault(pid, []).append(path)
            for pid, pname in prows:
                roots = root_map.get(pid, [])
                codex_projects.append({
                    "id": pid,
                    "name": pname,
                    "roots": roots,
                    "norm_roots": [norm_path(r) for r in roots if r],
                })

            thread_rows = con.execute(
                """
                SELECT id, title, cwd, archived, is_pinned, source, rollout_path, updated_at, project_id
                FROM threads
                WHERE source NOT LIKE '{%' AND attached_thread_parent_id IS NULL
                ORDER BY updated_at DESC
                """
            ).fetchall()
    except Exception:
        return {
            "existing_same_path": [],
            "new_codex_projects": [],
            "virtual_or_scratch_projects": [],
            "matched_projects": [],
        }

    all_threads: List[Dict[str, Any]] = []
    for tid, title, cwd, archived, is_pinned, source, rollout_path, updated_at, proj_id in thread_rows:
        all_threads.append({
            "thread_id": tid,
            "title": (title or "(untitled)").strip().replace("\r", " ").replace("\n", " ")[:120],
            "cwd": cwd or "",
            "norm_cwd": norm_path(cwd) if cwd else "",
            "archived": bool(archived),
            "is_pinned": bool(is_pinned),
            "source": source or "",
            "rollout_path": rollout_path or "",
            "rollout_exists": bool(rollout_path and os.path.isfile(rollout_path)),
            "updated_at": updated_at,
            "codex_project_id": proj_id,
        })

    # Map normalized root path -> existing Antigravity project
    agy_by_root: Dict[str, Dict[str, Any]] = {}
    agy_existing_names_lower: Set[str] = set()
    for ap in agy_projects:
        agy_existing_names_lower.add(ap["name"].lower())
        for nr in ap["norm_roots"]:
            agy_by_root[nr] = ap

    def collect_threads_for_roots(codex_proj: Dict[str, Any], roots_norm: Set[str]) -> List[Dict[str, Any]]:
        proj_basename = codex_proj["name"].lower()
        codex_pid = codex_proj["id"]
        matched: List[Dict[str, Any]] = []
        seen_tids: Set[str] = set()
        for t in all_threads:
            hit = False
            if t["codex_project_id"] == codex_pid:
                hit = True
            elif t["norm_cwd"] and t["norm_cwd"] in roots_norm:
                hit = True
            elif t["norm_cwd"] and "/.codex/worktrees/" in t["norm_cwd"] and os.path.basename(t["norm_cwd"]).lower() == proj_basename:
                hit = True
            if hit and t["thread_id"] not in seen_tids:
                seen_tids.add(t["thread_id"])
                matched.append(t)
        return matched

    export_base = os.path.join(gemini_config, "plugins", target_plugin, "project_imports")
    existing_same_path: List[Dict[str, Any]] = []
    new_codex_projects: List[Dict[str, Any]] = []
    virtual_or_scratch: List[Dict[str, Any]] = []

    for cp in codex_projects:
        roots = cp["roots"]
        norm_roots = set(cp["norm_roots"])

        # Priority 1: Does any root match an existing Antigravity project path?
        matched_ap: Optional[Dict[str, Any]] = None
        for nr in cp["norm_roots"]:
            if nr in agy_by_root:
                matched_ap = agy_by_root[nr]
                break

        raw_threads = collect_threads_for_roots(cp, norm_roots)

        if matched_ap is not None:
            agy_pid = matched_ap["id"]
            agy_pname = matched_ap["name"]
            imported_ids = list_imported_codex_thread_ids_by_project(agy_pid)
            proj_export_dir = os.path.join(export_base, re.sub(r"[^A-Za-z0-9._-]+", "_", agy_pname))

            active_m, archived_m, already_m = [], [], []
            for t in raw_threads:
                tid = t["thread_id"]
                if tid in ledger_synced or tid in imported_ids or os.path.isfile(os.path.join(proj_export_dir, f"{tid}.md")):
                    already_m.append(tid)
                    continue
                item = {k: t[k] for k in ("thread_id", "title", "cwd", "archived", "is_pinned", "source", "rollout_path", "rollout_exists", "updated_at")}
                (archived_m if t["archived"] else active_m).append(item)

            existing_same_path.append({
                "action": "sync_into_existing_project",
                "create_project": False,
                "agy_project_id": agy_pid,
                "agy_project_name": agy_pname,
                "project_roots": matched_ap["roots"],
                "codex_project_id": cp["id"],
                "codex_project_name": cp["name"],
                "default_selected": True,
                "active_threads_default_selected": True,
                "archived_threads_default_selected": False,
                "active_threads_count": len(active_m),
                "archived_threads_count": len(archived_m),
                "already_migrated_count": len(already_m),
                "active_threads": active_m,
                "archived_threads": archived_m,
            })
            continue

        # Not matched by path -> check whether it's a real local directory vs virtual/scratch
        real_roots = [r for r in roots if r and os.path.isdir(r) and not is_virtual_or_scratch_root(r)]
        is_virtual = not real_roots

        # Choose a clean non-colliding display name if creating a new project in Antigravity
        proposed_name = cp["name"]
        if proposed_name.lower() in agy_existing_names_lower and real_roots:
            parent_hint = os.path.basename(os.path.dirname(real_roots[0]))
            folder_hint = os.path.basename(real_roots[0])
            suffix = folder_hint if folder_hint.lower() != proposed_name.lower() else (parent_hint or "Codex")
            proposed_name = f"{cp['name']} ({suffix})"

        proj_export_dir = os.path.join(export_base, re.sub(r"[^A-Za-z0-9._-]+", "_", proposed_name))
        active_m, archived_m, already_m = [], [], []
        for t in raw_threads:
            tid = t["thread_id"]
            if tid in ledger_synced or os.path.isfile(os.path.join(proj_export_dir, f"{tid}.md")):
                already_m.append(tid)
                continue
            item = {k: t[k] for k in ("thread_id", "title", "cwd", "archived", "is_pinned", "source", "rollout_path", "rollout_exists", "updated_at")}
            (archived_m if t["archived"] else active_m).append(item)

        primary_root = real_roots[0] if real_roots else (roots[0] if roots else "")
        git_branch = detect_git_default_branch(primary_root) if (real_roots and primary_root) else None

        entry = {
            "action": "create_new_project_and_sync" if not is_virtual else "virtual_or_scratch",
            "create_project": not is_virtual,
            "agy_project_id": None,
            "agy_project_name": proposed_name,
            "project_roots": real_roots if real_roots else roots,
            "git_default_branch": git_branch,
            "codex_project_id": cp["id"],
            "codex_project_name": cp["name"],
            "default_selected": (not is_virtual) and (len(active_m) > 0 or len(archived_m) > 0),
            "active_threads_default_selected": True,
            "archived_threads_default_selected": False,
            "active_threads_count": len(active_m),
            "archived_threads_count": len(archived_m),
            "already_migrated_count": len(already_m),
            "active_threads": active_m,
            "archived_threads": archived_m,
        }
        if is_virtual:
            virtual_or_scratch.append(entry)
        else:
            new_codex_projects.append(entry)

    # Keep `matched_projects` as combined list for backward compatibility
    combined = existing_same_path + new_codex_projects
    return {
        "existing_same_path": existing_same_path,
        "new_codex_projects": new_codex_projects,
        "virtual_or_scratch_projects": virtual_or_scratch,
        "matched_projects": combined,
    }


def scan_non_migratable_extras(codex_home: str) -> Dict[str, Any]:
    extras: Dict[str, Any] = {}

    hooks_path = os.path.join(codex_home, "hooks.json")
    if os.path.isfile(hooks_path):
        try:
            with open(hooks_path, "r", encoding="utf-8") as f:
                hk = json.load(f)
            events = list((hk.get("hooks") or {}).keys()) if isinstance(hk, dict) else []
            extras["hooks_json"] = {
                "path": hooks_path,
                "events": events,
                "reason": "codex_specific_hook_schema",
            }
        except Exception:
            pass

    default_rules = os.path.join(codex_home, "rules", "default.rules")
    if os.path.isfile(default_rules):
        extras["codex_prefix_rules"] = {
            "path": default_rules,
            "size_bytes": os.path.getsize(default_rules),
            "reason": "codex_specific_approval_syntax",
        }

    auto_count = 0
    if sqlite3 is not None:
        for db_name in ("codex.db", "codex-dev.db"):
            db_path = os.path.join(codex_home, "sqlite", db_name)
            if os.path.isfile(db_path):
                try:
                    with open_codex_sqlite_readonly(db_path) as con:
                        if con is not None:
                            row = con.execute("SELECT count(*) FROM automations").fetchone()
                            if row and row[0]:
                                auto_count += int(row[0])
                except Exception:
                    pass
    extras["automations_count"] = auto_count

    return extras


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Codex -> Antigravity migration scanner")
    parser.add_argument("--codex-home", default="~/.codex")
    parser.add_argument("--agents-home", default="~/.agents")
    parser.add_argument("--gemini-config", default="~/.gemini/config")
    parser.add_argument("--agy-app-dir", default="~/.gemini/antigravity")
    parser.add_argument("--target-plugin", default="codex-imported")
    parser.add_argument("--ledger-dir", default=None)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    codex_home = expand(args.codex_home)
    agents_home = expand(args.agents_home)
    gemini_config = expand(args.gemini_config)
    agy_app_dir = expand(args.agy_app_dir)
    agy_builtin = os.path.join(agy_app_dir, "builtin")

    if not os.path.isdir(codex_home):
        print(json.dumps({"error": f"Codex directory not found: {codex_home}"}, ensure_ascii=False))
        return 1

    existing = discover_existing_agy_items(gemini_config, agy_builtin, args.target_plugin)
    prompts_res = scan_prompts(codex_home, existing)
    skills_res = scan_skills(codex_home, agents_home, existing)
    seen_skill_names = {s["name"] for s in skills_res["migratable"]}
    memory_res = scan_memory(codex_home, agy_app_dir, existing)
    third_party_res = scan_third_party_plugins_and_mcps(codex_home, existing, seen_skill_names)
    projects_res = scan_matching_projects(codex_home, gemini_config, args.target_plugin, args.ledger_dir)
    extras_res = scan_non_migratable_extras(codex_home)
    incomplete_runs = get_incomplete_runs(args.ledger_dir)

    report = {
        "codex_home": codex_home,
        "target_plugin": args.target_plugin,
        "target_plugin_exists": args.target_plugin in existing["plugins"],
        "incomplete_runs": incomplete_runs,
        "existing_antigravity_counts": {
            "skills": len(existing["skills"]),
            "mcps": len(existing["mcps"]),
            "plugins": len(existing["plugins"]),
        },
        "prompts": prompts_res,
        "skills": skills_res,
        "memory": memory_res,
        "third_party_plugins": third_party_res,
        "projects": projects_res,
        "non_migratable": extras_res,
    }

    indent = 2 if args.pretty else None
    print(json.dumps(report, ensure_ascii=False, indent=indent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
