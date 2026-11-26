#!/usr/bin/env python3
"""Transactional, idempotent, non-overwriting applicator for Codex -> Antigravity migration.

Core guarantees:
1. Exclusive process lock (flock) prevents concurrent / accidental double-runs.
2. SQLite idempotency ledger (`synced_sessions` PRIMARY KEY on `codex_thread_id`) +
   Antigravity tag check + disk check guarantees a Codex session can NEVER be synced twice.
3. Transactional journal (`migration_actions`) records every created artifact/conversation so any
   run can be cleanly undone via `rollback_migration.py`.
4. Interrupt auto-rollback (default ON): SIGINT / SIGTERM / mid-run failure automatically rolls
   back every change made during the current run so no partial dirty state is left behind.
5. Never modifies/deletes ~/.codex or ~/.agents, never overwrites existing items, never prints secrets.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import signal
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from ledger import (
    JsonLedger,
    call_conversation_service,
    migration_lock,
    rollback_run,
)

RULE_BYTE_LIMIT = 24000
ALLOWED_MCP_KEYS = {"command", "args", "env", "cwd", "url", "headers", "disabled", "variables"}


class MigrationInterrupted(BaseException):
    """Raised when SIGINT/SIGTERM is received during migration."""


def expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def list_imported_codex_thread_ids_by_project(project_id: str) -> Set[str]:
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
        resp, err = call_conversation_service("ListConversations", body)
        if err or not resp:
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


def collect_existing_agy_names(gemini_config: str, agy_builtin: str) -> Tuple[Set[str], Set[str]]:
    skills: Set[str] = set()
    mcps: Set[str] = set()

    for root in (
        os.path.join(gemini_config, "skills"),
        os.path.join(agy_builtin, "skills"),
    ):
        if os.path.isdir(root):
            for e in os.listdir(root):
                if os.path.exists(os.path.join(root, e, "SKILL.md")):
                    skills.add(e)

    global_mcp = os.path.join(gemini_config, "mcp_config.json")
    if os.path.isfile(global_mcp) and os.path.getsize(global_mcp) > 0:
        try:
            with open(global_mcp, "r", encoding="utf-8") as f:
                mcps.update((json.load(f).get("mcpServers") or {}).keys())
        except Exception:
            pass

    plugins_dir = os.path.join(gemini_config, "plugins")
    if os.path.isdir(plugins_dir):
        for pdir in os.listdir(plugins_dir):
            full = os.path.join(plugins_dir, pdir)
            if not os.path.isdir(full):
                continue
            pskills = os.path.join(full, "skills")
            if os.path.isdir(pskills):
                for e in os.listdir(pskills):
                    if os.path.exists(os.path.join(pskills, e, "SKILL.md")):
                        skills.add(e)
            pmcp = os.path.join(full, "mcp_config.json")
            if os.path.isfile(pmcp) and os.path.getsize(pmcp) > 0:
                try:
                    with open(pmcp, "r", encoding="utf-8") as f:
                        mcps.update((json.load(f).get("mcpServers") or {}).keys())
                except Exception:
                    pass

    return skills, mcps


def ensure_skill_frontmatter(skill_md_path: str, folder_name: str) -> None:
    with open(skill_md_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    has_fm = False
    fm_lines: List[str] = []
    body = text
    if text.startswith("---"):
        lines = text.splitlines()
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                has_fm = True
                fm_lines = lines[1:i]
                body = "\n".join(lines[i + 1 :])
                break

    if has_fm:
        has_name = any(re.match(r"^name\s*:", l) for l in fm_lines)
        has_desc = any(re.match(r"^description\s*:", l) for l in fm_lines)
        new_fm = []
        for l in fm_lines:
            if re.match(r"^name\s*:", l):
                new_fm.append(f"name: {folder_name}")
            else:
                new_fm.append(l)
        if not has_name:
            new_fm.insert(0, f"name: {folder_name}")
        if not has_desc:
            first_line = next((x.strip().lstrip("# ") for x in body.splitlines() if x.strip()), f"Migrated skill {folder_name}")
            safe_desc = first_line[:160].replace('"', '\\"')
            new_fm.append(f'description: "{safe_desc}"')
        updated = "---\n" + "\n".join(new_fm) + "\n---\n" + body.lstrip("\n")
    else:
        first_line = next((x.strip().lstrip("# ") for x in text.splitlines() if x.strip()), f"Migrated skill {folder_name}")
        safe_desc = first_line[:160].replace('"', '\\"')
        updated = f'---\nname: {folder_name}\ndescription: "{safe_desc}"\n---\n\n{text}'

    with open(skill_md_path, "w", encoding="utf-8") as f:
        f.write(updated)


def replace_plugin_root(val: Any, plugin_root: Optional[str]) -> Any:
    if not plugin_root:
        return val
    if isinstance(val, str):
        out = val.replace("${PLUGIN_ROOT}", plugin_root).replace("$PLUGIN_ROOT", plugin_root)
        if out.startswith("~/"):
            out = os.path.expanduser(out)
        out = out.replace("$HOME", os.path.expanduser("~"))
        return out
    if isinstance(val, list):
        return [replace_plugin_root(x, plugin_root) for x in val]
    if isinstance(val, dict):
        return {k: replace_plugin_root(v, plugin_root) for k, v in val.items()}
    return val


def normalize_placeholders(val: Any, declared_vars: Dict[str, Dict[str, str]]) -> Any:
    if isinstance(val, str):
        s = re.sub(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}", r"${\1}", val)

        def repl_default(m: re.Match) -> str:
            vname, default = m.group(1), m.group(2)
            declared_vars.setdefault(vname, {
                "name": vname,
                "title": vname.replace("_", " ").title(),
                "description": f"Value for {vname} (default in source: {default})",
            })
            return f"${{{vname}}}"

        s = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}", repl_default, s)
        for m in re.finditer(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", s):
            vname = m.group(1)
            declared_vars.setdefault(vname, {
                "name": vname,
                "title": vname.replace("_", " ").title(),
                "description": f"Environment variable or credential for {vname}",
            })
        return s
    if isinstance(val, list):
        return [normalize_placeholders(x, declared_vars) for x in val]
    if isinstance(val, dict):
        return {k: normalize_placeholders(v, declared_vars) for k, v in val.items()}
    return val


def convert_plugin_mcp_entry(raw_cfg: Dict[str, Any], plugin_root: Optional[str]) -> Tuple[Dict[str, Any], List[str]]:
    cfg = replace_plugin_root(raw_cfg, plugin_root)
    out: Dict[str, Any] = {}
    declared_vars: Dict[str, Dict[str, str]] = {}

    if cfg.get("command"):
        out["command"] = normalize_placeholders(cfg["command"], declared_vars)
    if cfg.get("args"):
        out["args"] = normalize_placeholders(cfg["args"], declared_vars)
    if cfg.get("cwd"):
        cwd = cfg["cwd"]
        if not os.path.isabs(cwd) and plugin_root:
            cwd = os.path.abspath(os.path.join(plugin_root, cwd))
        out["cwd"] = cwd
    if cfg.get("url"):
        out["url"] = normalize_placeholders(cfg["url"], declared_vars)
    if isinstance(cfg.get("headers"), dict):
        out["headers"] = normalize_placeholders(cfg["headers"], declared_vars)
    if isinstance(cfg.get("env"), dict):
        cleaned_env: Dict[str, str] = {}
        for k, v in cfg["env"].items():
            if not isinstance(v, str):
                cleaned_env[k] = str(v)
                continue
            if v in (f"${{{k}}}", f"${k}", f"${{env:{k}}}"):
                continue
            cleaned_env[k] = normalize_placeholders(v, declared_vars)
        if cleaned_env:
            out["env"] = cleaned_env
    if declared_vars:
        out["variables"] = list(declared_vars.values())

    out = {k: v for k, v in out.items() if k in ALLOWED_MCP_KEYS}
    return out, list(declared_vars.keys())


def parse_toml_single_mcp(config_toml_path: str, target_name: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(config_toml_path):
        return None
    with open(config_toml_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    cfg: Dict[str, Any] = {}
    in_target = False
    sub = ""
    found = False

    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^\[+([^\]]+)\]+$", s)
        if m:
            sec = m.group(1).strip()
            if sec.startswith("mcp_servers."):
                parts = [p.strip().strip('"').strip("'") for p in sec.split(".")]
                if parts[1] == target_name:
                    in_target = True
                    found = True
                    sub = ".".join(parts[2:]) if len(parts) > 2 else ""
                else:
                    in_target = False
                    sub = ""
            else:
                in_target = False
                sub = ""
            continue
        if not in_target or "=" not in s:
            continue
        k, v = [x.strip() for x in s.split("=", 1)]
        k_clean = k.strip('"').strip("'")
        v_raw = v.split("#", 1)[0].strip()

        def parse_val(vr: str) -> Any:
            if vr.startswith("[") and vr.endswith("]"):
                try:
                    return json.loads(vr)
                except Exception:
                    return [x.strip().strip('"').strip("'") for x in vr[1:-1].split(",") if x.strip()]
            if vr.lower() in ("true", "false"):
                return vr.lower() == "true"
            return vr.strip('"').strip("'")

        val = parse_val(v_raw)
        if sub == "env":
            cfg.setdefault("env", {})[k_clean] = val
        elif sub == "http_headers":
            cfg.setdefault("headers", {})[k_clean] = val
        elif sub == "env_http_headers":
            cfg.setdefault("headers", {})[k_clean] = f"${{{val}}}"
        elif not sub:
            if k_clean == "bearer_token_env_var":
                cfg.setdefault("headers", {})["Authorization"] = f"Bearer ${{{val}}}"
            elif k_clean in ("command", "args", "cwd", "url"):
                cfg[k_clean] = val
    return cfg if found else None


def extract_rollout_messages(rollout_path: str) -> List[Tuple[str, str]]:
    if not rollout_path or not os.path.isfile(rollout_path):
        return []
    msgs: List[Tuple[str, str]] = []
    with open(rollout_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") != "response_item":
                continue
            p = obj.get("payload")
            if not isinstance(p, dict) or p.get("type") != "message":
                continue
            role = p.get("role")
            if role not in ("user", "assistant"):
                continue
            parts: List[str] = []
            for c in p.get("content") or []:
                if isinstance(c, dict) and isinstance(c.get("text"), str):
                    parts.append(c["text"])
            text = "\n".join(parts).strip()
            if not text:
                continue
            if role == "user" and (
                text.startswith("# AGENTS.md instructions for ")
                or text.startswith("<environment_context>")
                or text.startswith("<INSTRUCTIONS>")
            ):
                continue
            msgs.append((role, text))
    return msgs


def format_transcript_markdown(thread: Dict[str, Any], msgs: List[Tuple[str, str]], agy_project_name: str) -> str:
    ts = thread.get("updated_at")
    updated_str = str(ts)
    if isinstance(ts, (int, float)) and ts > 0:
        try:
            updated_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    lines = [
        f"# {thread.get('title') or '(untitled)'}",
        "",
        f"- **Codex Thread ID**: `{thread['thread_id']}`",
        f"- **Antigravity Project**: `{agy_project_name}`",
        f"- **Source CWD**: `{thread.get('cwd') or ''}`",
        f"- **Archived in Codex**: `{bool(thread.get('archived'))}`",
        f"- **Last Updated**: `{updated_str}`",
        f"- **Extracted Messages**: `{len(msgs)}`",
        "",
        "---",
        "",
    ]
    if not msgs:
        lines.append("_No user/assistant messages were extracted from the source rollout file._\n")
    else:
        for idx, (role, text) in enumerate(msgs, 1):
            label = "User" if role == "user" else "Assistant"
            lines.append(f"## {idx}. {label}\n")
            lines.append(text)
            lines.append("")
    return "\n".join(lines)


def build_handoff_prompt(thread: Dict[str, Any], msgs: List[Tuple[str, str]], transcript_path: str) -> str:
    excerpt_lines: List[str] = []
    picked: List[Tuple[str, str]] = []
    if msgs:
        first_user = next((m for m in msgs if m[0] == "user"), None)
        tail = msgs[-4:]
        if first_user and first_user not in tail:
            picked.append(first_user)
        picked.extend(tail)
    for role, text in picked:
        tag = "User" if role == "user" else "Assistant"
        clipped = text if len(text) <= 800 else (text[:800] + "\n...[truncated]...")
        excerpt_lines.append(f"### {tag}\n{clipped}")

    excerpt_block = "\n\n".join(excerpt_lines) if excerpt_lines else "(No message excerpts extracted.)"
    return (
        "【Codex 历史会话迁移交接 · 仅记录上下文】\n"
        f"- 原标题：{thread.get('title') or '(untitled)'}\n"
        f"- Codex Thread ID：`{thread['thread_id']}`\n"
        f"- 原归档状态：`{'已归档' if thread.get('archived') else '未归档'}`\n"
        f"- 完整对话档案（只读）：`{transcript_path}`\n\n"
        "以下为关键对话摘录：\n\n"
        f"{excerpt_block}\n\n"
        "---\n"
        "指令：这是一次静态历史上下文导入。请**不要执行任何命令、不要修改任何文件、不要调用外部工具**。"
        "仅用 1-2 句话确认已载入该历史会话上下文，等待我后续提问。"
    )


def write_memory_lookup_skill(skills_root: str, codex_home: str, dry_run: bool) -> str:
    skill_dir = os.path.join(skills_root, "codex-memory-lookup")
    skill_md = os.path.join(skill_dir, "SKILL.md")
    content = f"""---
name: codex-memory-lookup
description: Read-only lookup into historical Codex memory files under {codex_home}/memories and memories_v2 when the user asks about prior Codex context, preferences, or past project notes.
---

# Codex Historical Memory Lookup (Read-Only)

Use this skill when the user asks you to recall prior context, preferences, or historical debugging notes stored in Codex memories.

## Rules

- Treat `{codex_home}/memories/` and `{codex_home}/memories_v2/` as **strictly read-only**. Never modify or delete files there.
- Start by reading the compact summaries first:
  - `{codex_home}/memories_v2/memory_summary.md`
  - `{codex_home}/memories/memory_summary.md`
- Only if a specific topic is mentioned in the summaries or requested by the user, search targeted lines in `{codex_home}/memories/MEMORY.md` or read the relevant file under `rollout_summaries/` (do not dump the entire file into context).
- Always distinguish historical memory notes from current live code/state, and verify current files/branches before acting.
"""
    if not dry_run:
        os.makedirs(skill_dir, exist_ok=True)
        with open(skill_md, "w", encoding="utf-8") as f:
            f.write(content)
    return skill_md


class RunJournal:
    """Records every created artifact into atomic JSON ledger immediately so interrupts can roll back cleanly."""

    def __init__(self, ledger: Optional[JsonLedger], run_id: str, dry_run: bool, interrupt_after: Optional[int] = None):
        self.ledger = ledger
        self.run_id = run_id
        self.dry_run = dry_run
        self.seq = 0
        self.interrupt_after = interrupt_after

    def record(
        self,
        kind: str,
        item_name: Optional[str] = None,
        target_path: Optional[str] = None,
        mcp_server_name: Optional[str] = None,
        conversation_id: Optional[str] = None,
        codex_thread_id: Optional[str] = None,
        agy_project_id: Optional[str] = None,
    ) -> None:
        self.seq += 1
        if not self.dry_run and self.ledger is not None:
            self.ledger.append_action(
                self.run_id,
                {
                    "kind": kind,
                    "item_name": item_name,
                    "target_path": target_path,
                    "mcp_server_name": mcp_server_name,
                    "conversation_id": conversation_id,
                    "codex_thread_id": codex_thread_id,
                    "agy_project_id": agy_project_id,
                },
            )
        if self.interrupt_after is not None and self.seq >= self.interrupt_after:
            raise MigrationInterrupted(f"Simulated interrupt triggered after action #{self.seq}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply confirmed Codex -> Antigravity migration plan with idempotency & auto-rollback")
    parser.add_argument("--plan", required=True, help="Path to JSON migration plan file")
    parser.add_argument("--gemini-config", default="~/.gemini/config")
    parser.add_argument("--agy-app-dir", default="~/.gemini/antigravity")
    parser.add_argument("--codex-home", default="~/.codex")
    parser.add_argument("--ledger-dir", default=None, help="Override ledger directory (for testing)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-rollback-on-interrupt", action="store_true", help="Disable default auto-rollback on interrupt")
    parser.add_argument("--simulate-interrupt-after", type=int, default=None, help="Testing flag: trigger interrupt after N recorded actions")
    args = parser.parse_args()

    with open(expand(args.plan), "r", encoding="utf-8") as f:
        plan = json.load(f)

    dry_run = args.dry_run or bool(plan.get("dry_run", False))
    rollback_on_interrupt = not args.no_rollback_on_interrupt
    gemini_config = expand(args.gemini_config)
    agy_builtin = os.path.join(expand(args.agy_app_dir), "builtin")
    codex_home = expand(args.codex_home)

    target_name = plan.get("target_plugin_name", "codex-imported").strip()
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", target_name):
        print(json.dumps({"error": f"Invalid target_plugin_name (must be lowercase kebab-case): {target_name}"}, ensure_ascii=False))
        return 1

    target_dir = os.path.join(gemini_config, "plugins", target_name)
    run_id = f"run-{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"

    def handle_signal(signum: int, _frame: Any) -> None:
        raise MigrationInterrupted(f"Received signal {signum}")

    prev_sigint = signal.signal(signal.SIGINT, handle_signal)
    prev_sigterm = signal.signal(signal.SIGTERM, handle_signal)

    try:
        with migration_lock(args.ledger_dir):
            ledger: Optional[JsonLedger] = None if dry_run else JsonLedger(args.ledger_dir, readonly=False)
            journal = RunJournal(ledger, run_id, dry_run, interrupt_after=args.simulate_interrupt_after)

            if not dry_run and ledger is not None:
                ledger.start_run(run_id, target_name, target_dir)

            existing_skills, existing_mcps = collect_existing_agy_names(gemini_config, agy_builtin)
            result: Dict[str, Any] = {
                "run_id": run_id,
                "status": "dry_run" if dry_run else "running",
                "dry_run": dry_run,
                "rollback_on_interrupt": rollback_on_interrupt,
                "target_plugin_name": target_name,
                "target_plugin_dir": target_dir,
                "created": [],
                "skipped_existing": [],
                "skipped_idempotent": [],
                "warnings": [],
                "variables_required": {},
            }

            try:
                # 0. Plugin dir & manifest
                if not os.path.exists(target_dir):
                    if not dry_run:
                        os.makedirs(target_dir, exist_ok=True)
                    journal.record("plugin_dir", item_name=target_name, target_path=target_dir)

                plugin_json_path = os.path.join(target_dir, "plugin.json")
                if not os.path.exists(plugin_json_path):
                    plugin_manifest = {
                        "name": target_name,
                        "displayName": plan.get("display_name") or "Codex Migrated Settings",
                        "version": "1.0.0",
                        "description": "Prompts, skills, memory summaries, project imports, and compatible MCP servers migrated from Codex via codex2agy.",
                    }
                    if not dry_run:
                        os.makedirs(target_dir, exist_ok=True)
                        with open(plugin_json_path, "w", encoding="utf-8") as f:
                            json.dump(plugin_manifest, f, ensure_ascii=False, indent=2)
                            f.write("\n")
                    journal.record("plugin_manifest", item_name="plugin.json", target_path=plugin_json_path)
                    result["created"].append({"kind": "plugin_manifest", "path": plugin_json_path})

                # 1. Prompts & AGENTS.md
                for p in plan.get("prompts") or []:
                    kind = p.get("kind")
                    src = expand(p["source_path"])
                    if not os.path.isfile(src):
                        result["warnings"].append(f"Prompt source missing: {src}")
                        continue
                    if kind == "instruction_rule":
                        rule_name = p.get("target_name") or "codex-agents.md"
                        dest = os.path.join(target_dir, "rules", rule_name)
                        if os.path.exists(dest):
                            result["skipped_existing"].append({"kind": "rule", "name": rule_name, "path": dest})
                            continue
                        size = os.path.getsize(src)
                        if size > RULE_BYTE_LIMIT:
                            result["warnings"].append(f"Skipped {src}: size {size}B exceeds 24KB rule limit")
                            continue
                        with open(src, "r", encoding="utf-8", errors="replace") as f:
                            body = f.read()
                        trigger = p.get("trigger") or "always_on"
                        fm = f"---\ntrigger: {trigger}\n---\n\n"
                        if not dry_run:
                            os.makedirs(os.path.dirname(dest), exist_ok=True)
                            with open(dest, "w", encoding="utf-8") as f:
                                f.write(fm + body)
                        journal.record("rule", item_name=rule_name, target_path=dest)
                        result["created"].append({"kind": "rule", "name": rule_name, "path": dest})
                    elif kind == "prompt_to_skill":
                        sname = p["target_name"]
                        dest_dir = os.path.join(target_dir, "skills", sname)
                        if sname in existing_skills or os.path.exists(dest_dir):
                            result["skipped_existing"].append({"kind": "skill", "name": sname, "reason": "already_exists"})
                            continue
                        with open(src, "r", encoding="utf-8", errors="replace") as f:
                            body = f.read()
                        body = re.sub(r"\$ARGUMENTS|\$1|\{\{args\}\}", "Apply this to what the user asked for.", body)
                        desc = (p.get("description") or f"Migrated Codex prompt {sname}").replace('"', '\\"')
                        content = f'---\nname: {sname}\ndescription: "{desc}"\n---\n\n{body}'
                        dest_md = os.path.join(dest_dir, "SKILL.md")
                        if not dry_run:
                            os.makedirs(dest_dir, exist_ok=True)
                            with open(dest_md, "w", encoding="utf-8") as f:
                                f.write(content)
                        existing_skills.add(sname)
                        journal.record("prompt_skill", item_name=sname, target_path=dest_dir)
                        result["created"].append({"kind": "prompt_skill", "name": sname, "path": dest_md})

                # 2. Skills (Codex/Agents + 3rd-party plugin skills)
                for s in plan.get("skills") or []:
                    sname = s["name"]
                    src_dir = expand(s["source_path"])
                    dest_dir = os.path.join(target_dir, "skills", sname)
                    if not os.path.isdir(src_dir) or not os.path.exists(os.path.join(src_dir, "SKILL.md")):
                        result["warnings"].append(f"Skill source invalid or missing SKILL.md: {src_dir}")
                        continue
                    if sname in existing_skills or os.path.exists(dest_dir):
                        result["skipped_existing"].append({"kind": "skill", "name": sname, "reason": "already_exists"})
                        continue
                    if not dry_run:
                        os.makedirs(os.path.join(target_dir, "skills"), exist_ok=True)
                        shutil.copytree(src_dir, dest_dir)
                        ensure_skill_frontmatter(os.path.join(dest_dir, "SKILL.md"), sname)
                    existing_skills.add(sname)
                    journal.record("skill", item_name=sname, target_path=dest_dir)
                    result["created"].append({"kind": "skill", "name": sname, "path": dest_dir})

                # 3. Memory summaries & optional lookup skill
                for m in plan.get("memory_rules") or []:
                    src = expand(m["source_path"])
                    rule_name = m["target_rule_name"]
                    dest = os.path.join(target_dir, "rules", rule_name)
                    if not os.path.isfile(src):
                        result["warnings"].append(f"Memory file missing: {src}")
                        continue
                    if os.path.exists(dest):
                        result["skipped_existing"].append({"kind": "memory_rule", "name": rule_name, "path": dest})
                        continue
                    size = os.path.getsize(src)
                    if size > RULE_BYTE_LIMIT:
                        result["warnings"].append(f"Skipped {src}: size {size}B exceeds 24KB rule limit")
                        continue
                    with open(src, "r", encoding="utf-8", errors="replace") as f:
                        body = f.read()
                    trigger = m.get("trigger") or "model_decision"
                    if trigger == "model_decision":
                        fm = (
                            "---\n"
                            "trigger: model_decision\n"
                            "description: Historical user preferences, project context, and tips migrated from Codex memory summary.\n"
                            "---\n\n"
                        )
                    else:
                        fm = "---\ntrigger: always_on\n---\n\n"
                    if not dry_run:
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with open(dest, "w", encoding="utf-8") as f:
                            f.write(fm + body)
                    journal.record("memory_rule", item_name=rule_name, target_path=dest)
                    result["created"].append({"kind": "memory_rule", "name": rule_name, "path": dest})

                if plan.get("create_memory_lookup_skill"):
                    sname = "codex-memory-lookup"
                    dest_dir = os.path.join(target_dir, "skills", sname)
                    if sname in existing_skills or os.path.exists(dest_dir):
                        result["skipped_existing"].append({"kind": "skill", "name": sname, "reason": "already_exists"})
                    else:
                        md_path = write_memory_lookup_skill(os.path.join(target_dir, "skills"), codex_home, dry_run)
                        existing_skills.add(sname)
                        journal.record("memory_lookup_skill", item_name=sname, target_path=dest_dir)
                        result["created"].append({"kind": "memory_lookup_skill", "name": sname, "path": md_path})

                # 4. MCP servers (merge without overwriting existing server names)
                mcp_items = plan.get("mcp_servers") or []
                if mcp_items:
                    mcp_path = os.path.join(target_dir, "mcp_config.json")
                    mcp_existed_before = os.path.isfile(mcp_path)
                    current_mcp: Dict[str, Any] = {"mcpServers": {}}
                    if mcp_existed_before and os.path.getsize(mcp_path) > 0:
                        with open(mcp_path, "r", encoding="utf-8") as f:
                            current_mcp = json.load(f)
                        current_mcp.setdefault("mcpServers", {})

                    mcp_file_recorded = False
                    for m in mcp_items:
                        mname = m["name"]
                        if mname in existing_mcps or mname in current_mcp["mcpServers"]:
                            result["skipped_existing"].append({"kind": "mcp", "name": mname, "reason": "already_exists"})
                            continue
                        skind = m.get("source_kind")
                        raw_cfg: Optional[Dict[str, Any]] = None
                        plugin_root = m.get("plugin_root")
                        if skind == "codex_plugin_mcp_json":
                            src_json = expand(m["source_path"])
                            if os.path.isfile(src_json):
                                with open(src_json, "r", encoding="utf-8") as f:
                                    loaded = json.load(f)
                                srv_map = loaded.get("mcpServers", loaded) if isinstance(loaded, dict) else {}
                                raw_cfg = srv_map.get(mname)
                        elif skind == "codex_config_toml":
                            raw_cfg = parse_toml_single_mcp(expand(m["source_path"]), mname)

                        if not isinstance(raw_cfg, dict):
                            result["warnings"].append(f"Could not read MCP definition for {mname}")
                            continue

                        converted, req_vars = convert_plugin_mcp_entry(raw_cfg, plugin_root)
                        current_mcp["mcpServers"][mname] = converted
                        existing_mcps.add(mname)
                        if not dry_run:
                            os.makedirs(target_dir, exist_ok=True)
                            with open(mcp_path, "w", encoding="utf-8") as f:
                                json.dump(current_mcp, f, ensure_ascii=False, indent=2)
                                f.write("\n")
                        if not mcp_existed_before and not mcp_file_recorded:
                            journal.record("mcp_file", item_name="mcp_config.json", target_path=mcp_path)
                            mcp_file_recorded = True
                        journal.record("mcp_entry", item_name=mname, target_path=mcp_path, mcp_server_name=mname)
                        if req_vars:
                            result["variables_required"][mname] = req_vars
                        result["created"].append({"kind": "mcp", "name": mname, "path": mcp_path})

                # 5. Matching & New Project migrations (strict session-level idempotency)
                seen_in_plan_threads: Set[str] = set()
                readonly_ledger = JsonLedger(args.ledger_dir, readonly=True) if dry_run else None
                projects_cfg_dir = os.path.join(gemini_config, "projects")

                def find_existing_project_by_root(roots: List[str]) -> Optional[Tuple[str, str]]:
                    norm_targets = {os.path.normpath(os.path.realpath(os.path.expanduser(r))) for r in roots if r}
                    if not norm_targets or not os.path.isdir(projects_cfg_dir):
                        return None
                    for fn in os.listdir(projects_cfg_dir):
                        if not fn.endswith(".json"):
                            continue
                        try:
                            with open(os.path.join(projects_cfg_dir, fn), "r", encoding="utf-8") as f:
                                pdata = json.load(f)
                        except Exception:
                            continue
                        for res in (pdata.get("projectResources") or {}).get("resources") or []:
                            uri = res.get("folderUri") or (res.get("gitFolder") or {}).get("folderUri")
                            if isinstance(uri, str) and uri.startswith("file://"):
                                nr = os.path.normpath(os.path.realpath(uri[len("file://") :]))
                                if nr in norm_targets:
                                    return pdata.get("id") or os.path.splitext(fn)[0], pdata.get("name") or ""
                    return None

                for pm in plan.get("project_migrations") or []:
                    agy_pid = pm.get("agy_project_id")
                    agy_pname = pm["agy_project_name"]
                    create_proj = bool(pm.get("create_project", False))
                    proj_roots = pm.get("project_roots") or []
                    git_branch = pm.get("git_default_branch")
                    active_mode = pm.get("conversation_mode") or "transcript_and_context"
                    archived_mode = pm.get("archived_conversation_mode") or (
                        "transcript_and_empty_thread" if active_mode == "transcript_and_context" else active_mode
                    )

                    valid_modes = ("transcript_and_context", "transcript_and_empty_thread", "transcript_only")
                    if active_mode not in valid_modes or archived_mode not in valid_modes:
                        result["warnings"].append(f"Unknown conversation_mode for project {agy_pname}; skipped")
                        continue

                    # Auto-create Antigravity project if requested and not already present for same path
                    if create_proj or not agy_pid:
                        existing_hit = find_existing_project_by_root(proj_roots)
                        if existing_hit:
                            agy_pid, agy_pname = existing_hit
                        else:
                            agy_pid = str(uuid.uuid4())
                            resources = []
                            for r in proj_roots:
                                if not r:
                                    continue
                                abs_r = os.path.abspath(os.path.expanduser(r))
                                file_uri = "file://" + abs_r.replace("\\", "/")
                                if git_branch or os.path.exists(os.path.join(abs_r, ".git")):
                                    resources.append({
                                        "gitFolder": {
                                            "folderUri": file_uri,
                                            "defaultBranch": git_branch or "main",
                                        }
                                    })
                                else:
                                    resources.append({"folderUri": file_uri})
                            proj_payload = {
                                "id": agy_pid,
                                "name": agy_pname,
                                "projectResources": {"resources": resources} if resources else {},
                                "permissionGrants": {"v2Migrated": True},
                                "settings": {},
                                "isWorkspaceOnly": False,
                            }
                            proj_file = os.path.join(projects_cfg_dir, f"{agy_pid}.json")
                            if not dry_run:
                                os.makedirs(projects_cfg_dir, exist_ok=True)
                                with open(proj_file, "w", encoding="utf-8") as f:
                                    json.dump(proj_payload, f, ensure_ascii=False, indent=2)
                                    f.write("\n")
                                # Brief wait so running Antigravity LS hot-loads the newly created project JSON
                                time.sleep(0.35)
                            journal.record(
                                "agy_project_file",
                                item_name=agy_pname,
                                target_path=proj_file,
                                agy_project_id=agy_pid,
                            )
                            result["created"].append({
                                "kind": "agy_project",
                                "name": agy_pname,
                                "agy_project_id": agy_pid,
                                "path": proj_file,
                                "project_roots": proj_roots,
                            })

                    safe_proj_dir = re.sub(r"[^A-Za-z0-9._-]+", "_", agy_pname)
                    export_dir = os.path.join(target_dir, "project_imports", safe_proj_dir)
                    imported_ids = list_imported_codex_thread_ids_by_project(agy_pid) if agy_pid else set()

                    for t in pm.get("threads") or []:
                        tid = t["thread_id"]
                        title = (t.get("title") or f"Codex {tid[:8]}").strip()[:100]
                        archived = bool(t.get("archived", False))
                        pinned = bool(t.get("is_pinned", False))
                        mode = archived_mode if archived else active_mode
                        transcript_path = os.path.join(export_dir, f"{tid}.md")

                        # Guard 1: duplicate inside the same plan payload
                        if tid in seen_in_plan_threads:
                            result["skipped_idempotent"].append({
                                "kind": "project_thread",
                                "project": agy_pname,
                                "thread_id": tid,
                                "title": title,
                                "reason": "duplicate_in_plan",
                            })
                            continue
                        seen_in_plan_threads.add(tid)

                        # Guard 2: remote Antigravity tag or existing transcript on disk
                        if tid in imported_ids or os.path.exists(transcript_path):
                            result["skipped_idempotent"].append({
                                "kind": "project_thread",
                                "project": agy_pname,
                                "thread_id": tid,
                                "title": title,
                                "reason": "already_exists_in_antigravity_or_disk",
                            })
                            continue

                        # Guard 3: Atomic JSON ledger key reservation (impossible for same session to sync twice)
                        if dry_run and readonly_ledger is not None:
                            existing_rec = (readonly_ledger.data.get("synced_sessions") or {}).get(tid)
                            if existing_rec:
                                result["skipped_idempotent"].append({
                                    "kind": "project_thread",
                                    "project": agy_pname,
                                    "thread_id": tid,
                                    "title": title,
                                    "reason": f"already_synced_in_ledger (run={existing_rec.get('run_id')}, status={existing_rec.get('status')})",
                                })
                                continue
                        elif not dry_run and ledger is not None:
                            if not ledger.reserve_session(tid, run_id, agy_pid, agy_pname, transcript_path):
                                result["skipped_idempotent"].append({
                                    "kind": "project_thread",
                                    "project": agy_pname,
                                    "thread_id": tid,
                                    "title": title,
                                    "reason": "blocked_by_idempotency_ledger_key",
                                })
                                continue

                        msgs = extract_rollout_messages(t.get("rollout_path") or "")
                        md_text = format_transcript_markdown(t, msgs, agy_pname)

                        if not dry_run:
                            os.makedirs(export_dir, exist_ok=True)
                            with open(transcript_path, "w", encoding="utf-8") as f:
                                f.write(md_text)
                        journal.record(
                            "project_transcript",
                            item_name=title,
                            target_path=transcript_path,
                            codex_thread_id=tid,
                            agy_project_id=agy_pid,
                        )

                        conv_id: Optional[str] = None
                        if mode in ("transcript_and_context", "transcript_and_empty_thread"):
                            create_req: Dict[str, Any] = {
                                "projectId": agy_pid,
                                "title": title,
                                "tags": ["codex-imported", f"codex-thread:{tid}"],
                                "model": "flash_lite",
                            }
                            if mode == "transcript_and_context":
                                create_req["prompt"] = [{"text": build_handoff_prompt(t, msgs, transcript_path)}]

                            if not dry_run:
                                resp, err = call_conversation_service("CreateConversation", create_req)
                                if err or not resp or not resp.get("conversation"):
                                    raise RuntimeError(f"CreateConversation failed for {agy_pname}/{tid}: {err}")
                                conv_id = resp["conversation"].get("id")
                                journal.record(
                                    "project_conversation",
                                    item_name=title,
                                    conversation_id=conv_id,
                                    codex_thread_id=tid,
                                    agy_project_id=agy_pid,
                                )
                                annotations: Dict[str, Any] = {}
                                if archived:
                                    annotations["archived"] = True
                                if pinned:
                                    annotations["pinned"] = True
                                if conv_id and annotations:
                                    _, uerr = call_conversation_service(
                                        "UpdateConversation",
                                        {"conversationId": conv_id, "annotations": annotations},
                                    )
                                    if uerr:
                                        result["warnings"].append(
                                            f"Created conversation {conv_id} for {tid}, but UpdateConversation failed: {uerr}"
                                        )

                        if not dry_run and ledger is not None:
                            ledger.mark_session_synced(tid, conv_id)

                        imported_ids.add(tid)
                        result["created"].append({
                            "kind": "project_thread",
                            "project": agy_pname,
                            "agy_project_id": agy_pid,
                            "thread_id": tid,
                            "title": title,
                            "archived": archived,
                            "mode": mode,
                            "transcript_path": transcript_path,
                            "conversation_id": conv_id,
                        })

                # Post-write JSON validation before commit
                if not dry_run:
                    for check_path in (plugin_json_path, os.path.join(target_dir, "mcp_config.json")):
                        if os.path.isfile(check_path):
                            with open(check_path, "r", encoding="utf-8") as f:
                                json.load(f)
                    if ledger is not None:
                        ledger.finish_run(run_id, "committed")
                    result["status"] = "committed"

                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0

            except (MigrationInterrupted, KeyboardInterrupt) as exc:
                if not dry_run and ledger is not None and rollback_on_interrupt:
                    rb = rollback_run(
                        ledger,
                        run_id,
                        final_status="interrupted_rolled_back",
                        reason=str(exc),
                        dry_run=False,
                    )
                    result["status"] = "interrupted_rolled_back"
                    result["interrupt_reason"] = str(exc)
                    result["rollback"] = rb
                else:
                    result["status"] = "interrupted"
                    result["interrupt_reason"] = str(exc)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 130

            except Exception as exc:
                if not dry_run and ledger is not None and rollback_on_interrupt:
                    rb = rollback_run(
                        ledger,
                        run_id,
                        final_status="failed_rolled_back",
                        reason=str(exc),
                        dry_run=False,
                    )
                    result["status"] = "failed_rolled_back"
                    result["error"] = str(exc)
                    result["rollback"] = rb
                else:
                    result["status"] = "failed"
                    result["error"] = str(exc)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 1
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)


if __name__ == "__main__":
    sys.exit(main())
