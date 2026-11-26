#!/usr/bin/env python3
"""Bilingual (EN/ZH) interactive terminal CLI for codex2agy (zero dependencies, Python 3.9+).

Subcommands:
  codex2agy [--lang auto|en|zh] scan [--json]
  codex2agy [--lang auto|en|zh] migrate [--dry-run] [--yes] [--include-archived]
  codex2agy [--lang auto|en|zh] history [--json]
  codex2agy [--lang auto|en|zh] undo [--latest | --run-id ID] [--dry-run] [--yes]
"""

import argparse
import json
import locale
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCAN_SCRIPT = os.path.join(SCRIPTS_DIR, "scan_codex.py")
APPLY_SCRIPT = os.path.join(SCRIPTS_DIR, "apply_migration.py")
ROLLBACK_SCRIPT = os.path.join(SCRIPTS_DIR, "rollback_migration.py")

STRINGS: Dict[str, Dict[str, str]] = {
    "zh": {
        "scan_title": "\n=== codex2agy 扫描报告 (只读) ===",
        "scan_meta": "Codex 目录: {codex}  |  目标插件: {plugin}",
        "incomplete_warn": "\n[!] 检测到 {n} 个未完成的中断批次，建议先运行 `codex2agy undo --latest` 清理:",
        "sec1": "\n1. 提示词 (默认不迁): {n} 项",
        "sec2": "\n2. Skills (默认全选): {n} 个净新增",
        "sec2_dup": "  |  自动跳过重复 {n} 个",
        "sec3": "\n3. 记忆 (默认不迁): {n} 份摘要 (<=24KB)",
        "sec3_note": "  |  Antigravity 无原生 MEMORY.md 导入接口，可转规则或只读检索 Skill",
        "sec4": "\n4. 兼容第三方插件 (默认全选): {ns} 个 Skills + {nm} 个 MCP",
        "sec5": "\n5. 项目与历史会话 (按本地路径自动匹配，未归档默认迁 / 已归档默认不迁):",
        "sec5_same": "   已有同路径项目 ({n} 个 · 直接合并会话):",
        "sec5_new": "   Codex 独有项目 ({n} 个 · 自动在 Antigravity 新建项目并同步会话):",
        "sec5_virt": "   虚拟/临时目录项目 ({n} 个 · 默认跳过): {names}",
        "proj_counts": "未归档 {a} 条 / 已归档 {r} 条 ",
        "proj_skipped": "({root}, 已迁跳过 {s})",
        "w1_title": "\n[1/5] 提示词 (默认: 不迁移)",
        "w1_ask": "输入编号迁入 (如 1 / all)，直接回车跳过 [默认不迁]: ",
        "w2_title": "\n[2/5] Skills (默认: 全选 {n} 个；已自动跳过 {d} 个重复项)",
        "w2_ask": "直接回车全选，或输入编号 / `-编号` 排除 / `none` 跳过 [默认全选]: ",
        "w3_title": "\n[3/5] 记忆对比 (Codex vs Antigravity)",
        "w3_codex": "  - Codex: 显式 ~/.codex/memories/ 文件",
        "w3_agy": "  - Antigravity: 无原生 MEMORY.md 导入接口，靠 Rules (<=24KB) 与按需 Skill 承载",
        "w3_opts": "  选项: 0=不迁移(默认)  1=迁入精炼摘要为按需规则  2=生成只读检索 Skill  3=两者都要",
        "w3_ask": "请选择 [0/1/2/3，默认 0]: ",
        "w4_title": "\n[4/5] 兼容第三方插件 Skills & MCP (默认: 全选 {n} 项)",
        "w5_title": "\n[5/5] 项目与历史会话 (默认全选以下 {n} 个真实本地项目，仅同步未归档会话)",
        "tag_same": "同路径·直接同步",
        "tag_new": "Codex 独有·自动新建项目",
        "w5_arch_ask": "选中项目共有 {n} 条【已归档】会话，是否一并迁移？(迁入后仍保持已归档，零模型消耗) [y/N，默认不迁]: ",
        "w5_mode_opts": "  未归档会话导入方式: 1=可续聊模式(完整档案+带上下文会话，默认)  2=轻量模式(仅建标题会话，零模型消耗)  3=仅导出 Markdown 档案",
        "w5_mode_ask": "请选择 [1/2/3，默认 1]: ",
        "w5_offline_note": "  [提示] 当前为独立外部终端（未检测到 Antigravity LS 会话接口），项目会话将导出为 Markdown 档案并写入新建项目配置；如需同步进侧边栏会话请在 Antigravity 对话或内置终端运行。",
        "plan_title": "\n=== 即将执行的迁移清单 ===",
        "plan_summary": "  提示词: {p}  |  Skills: {s}  |  记忆规则: {mr}  |  记忆检索Skill: {ml}  |  MCP: {m}  |  项目会话: {t}",
        "dry_note": "  模式: --dry-run (演练，不写入任何文件)",
        "confirm_run": "\n确认开始执行？(Ctrl+C 中断将默认自动撤销) [Y/n]: ",
        "cancelled": "已取消，未写入任何内容。",
        "no_history": "暂无迁移记录。",
        "history_title": "\n=== codex2agy 迁移历史 ===",
        "latest_run": "最近一次迁移",
        "confirm_undo": "确认撤回 {target}？（只删除该批次新建项，不动原有配置）[y/N]: ",
        "undo_cancelled": "已取消撤回。",
    },
    "en": {
        "scan_title": "\n=== codex2agy Scan Report (Read-Only) ===",
        "scan_meta": "Codex home: {codex}  |  Target plugin: {plugin}",
        "incomplete_warn": "\n[!] Detected {n} incomplete interrupted run(s). Consider running `codex2agy undo --latest` first:",
        "sec1": "\n1. Prompts (default: skip): {n} item(s)",
        "sec2": "\n2. Skills (default: select all): {n} net-new",
        "sec2_dup": "  |  auto-skipped {n} duplicate(s)",
        "sec3": "\n3. Memory (default: skip): {n} summary file(s) (<=24KB)",
        "sec3_note": "  |  Antigravity has no native MEMORY.md import; convert to Rules or a read-only lookup Skill",
        "sec4": "\n4. Compatible 3rd-Party Plugins (default: select all): {ns} Skill(s) + {nm} MCP(s)",
        "sec5": "\n5. Projects & Sessions (path-based; active default sync / archived default skip):",
        "sec5_same": "   Existing same-path projects ({n} · sync sessions directly):",
        "sec5_new": "   Codex-only local projects ({n} · auto-create in Antigravity & sync):",
        "sec5_virt": "   Virtual/scratch projects ({n} · skipped by default): {names}",
        "proj_counts": "active {a} / archived {r} ",
        "proj_skipped": "({root}, already synced {s})",
        "w1_title": "\n[1/5] Prompts (default: skip)",
        "w1_ask": "Enter numbers to include (e.g. 1 / all), or press Enter to skip [default: skip]: ",
        "w2_title": "\n[2/5] Skills (default: select all {n}; auto-skipped {d} duplicate(s))",
        "w2_ask": "Press Enter for all, or enter numbers / `-N` to exclude / `none` to skip [default: all]: ",
        "w3_title": "\n[3/5] Memory Comparison (Codex vs Antigravity)",
        "w3_codex": "  - Codex: explicit ~/.codex/memories/ files",
        "w3_agy": "  - Antigravity: no native MEMORY.md import; uses Rules (<=24KB) & on-demand Skills",
        "w3_opts": "  Options: 0=Skip (default)  1=Migrate summary as on-demand Rule  2=Create read-only lookup Skill  3=Both",
        "w3_ask": "Choose [0/1/2/3, default 0]: ",
        "w4_title": "\n[4/5] Compatible 3rd-Party Plugin Skills & MCP (default: select all {n})",
        "w5_title": "\n[5/5] Projects & Sessions (default: all {n} real local projects, active sessions only)",
        "tag_same": "same path · sync",
        "tag_new": "Codex-only · auto-create",
        "w5_arch_ask": "Selected projects have {n} archived session(s). Include them too? (stays archived, zero model calls) [y/N, default N]: ",
        "w5_mode_opts": "  Active session mode: 1=Continuable context (Markdown + sidebar chat, default)  2=Lightweight (title-only, zero model calls)  3=Markdown transcripts only",
        "w5_mode_ask": "Choose [1/2/3, default 1]: ",
        "w5_offline_note": "  [Note] Running outside Antigravity (LS API unavailable): sessions will export as Markdown transcripts and new project configs will be created. Run inside Antigravity chat/terminal to also populate sidebar conversations.",
        "plan_title": "\n=== Migration Summary ===",
        "plan_summary": "  Prompts: {p}  |  Skills: {s}  |  Memory Rules: {mr}  |  Memory Lookup Skill: {ml}  |  MCPs: {m}  |  Project Sessions: {t}",
        "dry_note": "  Mode: --dry-run (simulation only, no files written)",
        "confirm_run": "\nProceed? (Ctrl+C automatically rolls back) [Y/n]: ",
        "cancelled": "Cancelled. Nothing was written.",
        "no_history": "No migration runs recorded yet.",
        "history_title": "\n=== codex2agy Migration History ===",
        "latest_run": "the latest migration run",
        "confirm_undo": "Confirm rollback of {target}? (removes only items created by this run) [y/N]: ",
        "undo_cancelled": "Rollback cancelled.",
    },
}


def detect_lang(choice: str) -> str:
    if choice in ("zh", "en"):
        return choice
    env_lang = (os.environ.get("LC_ALL") or os.environ.get("LANG") or "").lower()
    if not env_lang:
        try:
            env_lang = (locale.getdefaultlocale()[0] or "").lower()
        except Exception:
            env_lang = ""
    return "zh" if env_lang.startswith("zh") else "en"


def use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if use_color() else text


def bold(t: str) -> str: return c(t, "1")
def dim(t: str) -> str: return c(t, "2")
def green(t: str) -> str: return c(t, "32")
def yellow(t: str) -> str: return c(t, "33")
def cyan(t: str) -> str: return c(t, "36")
def red(t: str) -> str: return c(t, "31")


def run_scan(args: argparse.Namespace) -> Dict[str, Any]:
    cmd = [sys.executable, SCAN_SCRIPT, "--target-plugin", args.target_plugin]
    if args.codex_home:
        cmd += ["--codex-home", args.codex_home]
    if args.gemini_config:
        cmd += ["--gemini-config", args.gemini_config]
    if args.ledger_dir:
        cmd += ["--ledger-dir", args.ledger_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or proc.stdout)
        sys.exit(proc.returncode)
    return json.loads(proc.stdout)


def print_scan_summary(data: Dict[str, Any], s: Dict[str, str]) -> None:
    print(bold(s["scan_title"]))
    print(dim(s["scan_meta"].format(codex=data["codex_home"], plugin=data["target_plugin"])))

    incomplete = data.get("incomplete_runs") or []
    if incomplete:
        print(yellow(s["incomplete_warn"].format(n=len(incomplete))))
        for r in incomplete:
            print(f"    - {r['run_id']} ({r['created_at']})")

    prompts = data["prompts"]["migratable"]
    print(bold(s["sec1"].format(n=len(prompts))))
    for i, p in enumerate(prompts, 1):
        kb = p["size_bytes"] / 1024
        warn = red(" [>24KB]") if p.get("exceeds_24kb_limit") else ""
        print(f"   [{i}] {p['name']} ({kb:.1f} KB){warn}")

    skills = data["skills"]["migratable"]
    dup_skills = data["skills"]["skipped_duplicates"]
    print(bold(s["sec2"].format(n=len(skills))) + dim(s["sec2_dup"].format(n=len(dup_skills))))
    for i, sk in enumerate(skills, 1):
        print(f"   [{i}] {cyan(sk['name'])} " + dim(f"({sk['source_group']})"))

    mem = data["memory"]
    sums = [m for m in mem["summary_candidates"] if not m.get("already_exists")]
    print(bold(s["sec3"].format(n=len(sums))) + dim(s["sec3_note"]))
    for i, m in enumerate(sums, 1):
        print(f"   [{i}] {m['id']} ({m['size_bytes']/1024:.1f} KB)")

    tp = data["third_party_plugins"]
    tp_skills = tp["plugin_skills_migratable"]
    tp_mcps = tp["mcps_migratable"]
    print(bold(s["sec4"].format(ns=len(tp_skills), nm=len(tp_mcps))))
    for i, sk in enumerate(tp_skills, 1):
        print(f"   [S{i}] {cyan(sk['name'])} " + dim(f"({sk.get('plugin_source', '')})"))
    for i, m in enumerate(tp_mcps, 1):
        print(f"   [M{i}] {green(m['name'])} " + dim(f"(MCP {m['transport']}, {m.get('plugin_source', m.get('source_kind'))})"))

    pinfo = data.get("projects") or {}
    same_path = pinfo.get("existing_same_path") or []
    new_projs = pinfo.get("new_codex_projects") or []
    virt_projs = pinfo.get("virtual_or_scratch_projects") or []
    print(bold(s["sec5"]))
    if same_path:
        print(green(s["sec5_same"].format(n=len(same_path))))
        for i, p in enumerate(same_path, 1):
            root_str = (p.get("project_roots") or [""])[0]
            print(
                f"     [E{i}] {bold(p['agy_project_name'])} "
                + s["proj_counts"].format(a=p["active_threads_count"], r=p["archived_threads_count"])
                + dim(s["proj_skipped"].format(root=root_str, s=p["already_migrated_count"]))
            )
    if new_projs:
        print(cyan(s["sec5_new"].format(n=len(new_projs))))
        for i, p in enumerate(new_projs, 1):
            root_str = (p.get("project_roots") or [""])[0]
            print(
                f"     [N{i}] {bold(p['agy_project_name'])} "
                + s["proj_counts"].format(a=p["active_threads_count"], r=p["archived_threads_count"])
                + dim(s["proj_skipped"].format(root=root_str, s=p["already_migrated_count"]))
            )
    if virt_projs:
        print(dim(s["sec5_virt"].format(n=len(virt_projs), names=", ".join(p["codex_project_name"] for p in virt_projs))))
    print()


def parse_index_selection(raw: str, total: int, default_all: bool) -> List[int]:
    val = raw.strip().lower()
    if not val:
        return list(range(total)) if default_all else []
    if val in ("all", "a", "*"):
        return list(range(total))
    if val in ("none", "n", "0", "skip"):
        return []
    tokens = [t.strip() for t in val.replace(" ", ",").split(",") if t.strip()]
    if all(t.startswith("-") for t in tokens):
        excluded = set()
        for t in tokens:
            try:
                idx = int(t[1:]) - 1
                if 0 <= idx < total:
                    excluded.add(idx)
            except ValueError:
                pass
        return [i for i in range(total) if i not in excluded]
    picked: List[int] = []
    for t in tokens:
        try:
            idx = int(t) - 1
            if 0 <= idx < total and idx not in picked:
                picked.append(idx)
        except ValueError:
            pass
    return picked


def ask(prompt: str, default: str = "") -> str:
    try:
        ans = input(prompt)
    except EOFError:
        return default
    return ans.strip() if ans.strip() else default


def build_plan_interactive(data: Dict[str, Any], args: argparse.Namespace, s: Dict[str, str]) -> Dict[str, Any]:
    auto_yes = args.yes
    ls_available = bool(os.environ.get("ANTIGRAVITY_LS_ADDRESS") and os.environ.get("ANTIGRAVITY_CSRF_TOKEN"))

    # 1. Prompts
    selected_prompts: List[Dict[str, Any]] = []
    prompts = data["prompts"]["migratable"]
    if prompts and not auto_yes:
        print(bold(s["w1_title"]))
        for i, p in enumerate(prompts, 1):
            print(f"  {i}. {p['name']} ({p['size_bytes']/1024:.1f} KB) — {dim(p.get('preview', '')[:70])}")
        ans = ask(s["w1_ask"])
        for idx in parse_index_selection(ans, len(prompts), default_all=False):
            selected_prompts.append(prompts[idx])

    # 2. Skills
    skills = data["skills"]["migratable"]
    selected_skills: List[Dict[str, Any]] = []
    if skills:
        if auto_yes:
            selected_skills = list(skills)
        else:
            print(bold(s["w2_title"].format(n=len(skills), d=len(data["skills"]["skipped_duplicates"]))))
            for i, sk in enumerate(skills, 1):
                print(f"  {i}. {cyan(sk['name'])} ({sk['source_group']})")
            ans = ask(s["w2_ask"])
            for idx in parse_index_selection(ans, len(skills), default_all=True):
                selected_skills.append(skills[idx])

    # 3. Memory
    selected_memory_rules: List[Dict[str, Any]] = []
    create_lookup = False
    mem_sums = [m for m in data["memory"]["summary_candidates"] if not m.get("already_exists") and m.get("fits_24kb_rule_limit")]
    if (mem_sums or data["memory"]["large_files"]) and not auto_yes:
        print(bold(s["w3_title"]))
        print(s["w3_codex"])
        print(s["w3_agy"])
        print(s["w3_opts"])
        ans = ask(s["w3_ask"], default="0")
        if ans in ("1", "3"):
            for m in mem_sums:
                selected_memory_rules.append({
                    "source_path": m["source_path"],
                    "target_rule_name": m["target_rule_name"],
                    "trigger": "model_decision",
                })
        if ans in ("2", "3"):
            create_lookup = True

    # 4. 3rd-party plugin skills & MCP
    tp = data["third_party_plugins"]
    tp_items: List[Dict[str, Any]] = []
    for sk in tp["plugin_skills_migratable"]:
        tp_items.append({"_kind": "skill", "item": sk})
    for m in tp["mcps_migratable"]:
        tp_items.append({"_kind": "mcp", "item": m})

    selected_mcps: List[Dict[str, Any]] = []
    if tp_items:
        if auto_yes:
            picked_indices = list(range(len(tp_items)))
        else:
            print(bold(s["w4_title"].format(n=len(tp_items))))
            for i, entry in enumerate(tp_items, 1):
                it = entry["item"]
                badge = cyan("Skill") if entry["_kind"] == "skill" else green("MCP  ")
                print(f"  {i}. [{badge}] {it['name']} " + dim(f"({it.get('plugin_source', it.get('source_kind', ''))})"))
            ans = ask(s["w2_ask"])
            picked_indices = parse_index_selection(ans, len(tp_items), default_all=True)
        for idx in picked_indices:
            entry = tp_items[idx]
            if entry["_kind"] == "skill":
                selected_skills.append(entry["item"])
            else:
                selected_mcps.append(entry["item"])

    # 5. Projects
    pinfo = data.get("projects") or {}
    real_projs = (pinfo.get("existing_same_path") or []) + (pinfo.get("new_codex_projects") or [])
    project_migrations: List[Dict[str, Any]] = []
    if real_projs:
        if auto_yes:
            picked_projs = [p for p in real_projs if p.get("default_selected", True)]
        else:
            print(bold(s["w5_title"].format(n=len(real_projs))))
            for i, p in enumerate(real_projs, 1):
                tag = green(s["tag_same"]) if not p.get("create_project") else cyan(s["tag_new"])
                root_str = (p.get("project_roots") or [""])[0]
                print(
                    f"  {i}. {bold(p['agy_project_name'])} [{tag}] "
                    + s["proj_counts"].format(a=p["active_threads_count"], r=p["archived_threads_count"])
                    + dim(f"| {root_str}")
                )
            ans = ask(s["w2_ask"])
            picked_projs = [real_projs[i] for i in parse_index_selection(ans, len(real_projs), default_all=True)]

        if picked_projs:
            include_archived = args.include_archived
            if not auto_yes and any(p["archived_threads_count"] > 0 for p in picked_projs):
                total_arch = sum(p["archived_threads_count"] for p in picked_projs)
                a_ans = ask(s["w5_arch_ask"].format(n=total_arch), default="n").lower()
                include_archived = a_ans in ("y", "yes")

            if ls_available:
                active_mode = "transcript_and_context"
                archived_mode = "transcript_and_empty_thread"
                if not auto_yes:
                    print(s["w5_mode_opts"])
                    m_ans = ask(s["w5_mode_ask"], default="1")
                    active_mode = {"1": "transcript_and_context", "2": "transcript_and_empty_thread", "3": "transcript_only"}.get(m_ans, "transcript_and_context")
                    archived_mode = "transcript_only" if active_mode == "transcript_only" else "transcript_and_empty_thread"
            else:
                active_mode = "transcript_only"
                archived_mode = "transcript_only"
                print(yellow(s["w5_offline_note"]))

            for p in picked_projs:
                threads = list(p["active_threads"])
                if include_archived:
                    threads.extend(p["archived_threads"])
                if threads or p.get("create_project"):
                    project_migrations.append({
                        "agy_project_id": p.get("agy_project_id"),
                        "agy_project_name": p["agy_project_name"],
                        "create_project": bool(p.get("create_project", False)),
                        "project_roots": p.get("project_roots") or [],
                        "git_default_branch": p.get("git_default_branch"),
                        "conversation_mode": active_mode,
                        "archived_conversation_mode": archived_mode,
                        "threads": threads,
                    })

    return {
        "target_plugin_name": args.target_plugin,
        "prompts": selected_prompts,
        "skills": selected_skills,
        "memory_rules": selected_memory_rules,
        "create_memory_lookup_skill": create_lookup,
        "mcp_servers": selected_mcps,
        "project_migrations": project_migrations,
    }


def cmd_scan(args: argparse.Namespace, s: Dict[str, str]) -> int:
    data = run_scan(args)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_scan_summary(data, s)
    return 0


def cmd_migrate(args: argparse.Namespace, s: Dict[str, str]) -> int:
    data = run_scan(args)
    print_scan_summary(data, s)
    plan = build_plan_interactive(data, args, s)

    total_threads = sum(len(pm["threads"]) for pm in plan["project_migrations"])
    print(bold(s["plan_title"]))
    print(
        s["plan_summary"].format(
            p=len(plan["prompts"]),
            s=len(plan["skills"]),
            mr=len(plan["memory_rules"]),
            ml=plan["create_memory_lookup_skill"],
            m=len(plan["mcp_servers"]),
            t=total_threads,
        )
    )
    if args.dry_run:
        print(yellow(s["dry_note"]))

    if not args.yes:
        confirm = ask(s["confirm_run"], default="y").lower()
        if confirm not in ("y", "yes"):
            print(s["cancelled"])
            return 0

    fd, plan_path = tempfile.mkstemp(prefix="codex2agy-plan-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        cmd = [sys.executable, APPLY_SCRIPT, "--plan", plan_path]
        if args.dry_run:
            cmd.append("--dry-run")
        if args.codex_home:
            cmd += ["--codex-home", args.codex_home]
        if args.gemini_config:
            cmd += ["--gemini-config", args.gemini_config]
        if args.ledger_dir:
            cmd += ["--ledger-dir", args.ledger_dir]
        return subprocess.run(cmd, text=True).returncode
    finally:
        if os.path.exists(plan_path):
            os.remove(plan_path)


def cmd_history(args: argparse.Namespace, s: Dict[str, str]) -> int:
    cmd = [sys.executable, ROLLBACK_SCRIPT, "--list"]
    if args.ledger_dir:
        cmd += ["--ledger-dir", args.ledger_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or proc.stdout)
        return proc.returncode
    if args.json:
        print(proc.stdout.strip())
        return 0
    runs = json.loads(proc.stdout).get("runs") or []
    if not runs:
        print(s["no_history"])
        return 0
    print(bold(s["history_title"]))
    for r in runs:
        print(
            f"  {cyan(r['run_id'])}  |  status={bold(r['status'])}  |  "
            f"plugin={r['target_plugin_name']}  |  actions={r['action_count']}  |  "
            f"sessions={r['synced_session_count']}  |  {dim(r['created_at'])}"
        )
    print()
    return 0


def cmd_undo(args: argparse.Namespace, s: Dict[str, str]) -> int:
    cmd = [sys.executable, ROLLBACK_SCRIPT]
    if args.run_id:
        cmd += ["--run-id", args.run_id]
    else:
        cmd.append("--latest")
    if args.dry_run:
        cmd.append("--dry-run")
    if args.ledger_dir:
        cmd += ["--ledger-dir", args.ledger_dir]
    if not args.dry_run and not args.yes:
        target = args.run_id or s["latest_run"]
        confirm = ask(s["confirm_undo"].format(target=target), default="n").lower()
        if confirm not in ("y", "yes"):
            print(s["undo_cancelled"])
            return 0
    return subprocess.run(cmd).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex2agy",
        description="Safe, interactive, bilingual (EN/ZH) Codex -> Antigravity migrator CLI",
    )
    parser.add_argument("--lang", choices=["auto", "en", "zh"], default="auto", help="UI language (default: auto)")
    parser.add_argument("--codex-home", default=None, help="Override ~/.codex path")
    parser.add_argument("--gemini-config", default=None, help="Override ~/.gemini/config path")
    parser.add_argument("--ledger-dir", default=None, help="Override idempotency ledger path")
    parser.add_argument("--target-plugin", default="codex-imported", help="Target plugin name (default: codex-imported)")

    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="Read-only scan of Codex assets & projects")
    p_scan.add_argument("--json", action="store_true", help="Output raw JSON")

    p_mig = sub.add_parser("migrate", help="Interactive migration wizard (default if no subcommand)")
    p_mig.add_argument("--dry-run", action="store_true", help="Simulate without writing files")
    p_mig.add_argument("--yes", "-y", action="store_true", help="Accept default safe selections non-interactively")
    p_mig.add_argument("--include-archived", action="store_true", help="Also include archived threads in --yes mode")

    p_hist = sub.add_parser("history", help="List past migration runs")
    p_hist.add_argument("--json", action="store_true", help="Output raw JSON")

    p_undo = sub.add_parser("undo", help="Rollback latest or specific migration run")
    p_undo.add_argument("--run-id", default=None, help="Specific run_id to rollback")
    p_undo.add_argument("--latest", action="store_true", help="Rollback latest active/committed run (default)")
    p_undo.add_argument("--dry-run", action="store_true", help="Preview rollback actions")
    p_undo.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    lang = detect_lang(args.lang)
    s = STRINGS[lang]
    cmd = args.command or "migrate"
    if cmd == "scan":
        return cmd_scan(args, s)
    if cmd == "migrate":
        if not hasattr(args, "dry_run"):
            args.dry_run = False
            args.yes = False
            args.include_archived = False
        return cmd_migrate(args, s)
    if cmd == "history":
        return cmd_history(args, s)
    if cmd == "undo":
        return cmd_undo(args, s)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
