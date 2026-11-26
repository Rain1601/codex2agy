#!/usr/bin/env python3
"""Interactive terminal CLI for codex2agy (zero third-party dependencies, Python 3.9+).

Subcommands:
  codex2agy scan [--json]
  codex2agy migrate [--dry-run] [--yes] [--include-archived] [--target-plugin NAME]
  codex2agy history
  codex2agy undo [--latest | --run-id ID] [--dry-run]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCAN_SCRIPT = os.path.join(SCRIPTS_DIR, "scan_codex.py")
APPLY_SCRIPT = os.path.join(SCRIPTS_DIR, "apply_migration.py")
ROLLBACK_SCRIPT = os.path.join(SCRIPTS_DIR, "rollback_migration.py")


def use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text: str, code: str) -> str:
    if not use_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(t: str) -> str:
    return c(t, "1")


def dim(t: str) -> str:
    return c(t, "2")


def green(t: str) -> str:
    return c(t, "32")


def yellow(t: str) -> str:
    return c(t, "33")


def cyan(t: str) -> str:
    return c(t, "36")


def red(t: str) -> str:
    return c(t, "31")


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


def print_scan_summary(data: Dict[str, Any]) -> None:
    print(bold("\n=== codex2agy 扫描报告 (只读) ==="))
    print(dim(f"Codex 目录: {data['codex_home']}  |  目标插件: {data['target_plugin']}"))

    incomplete = data.get("incomplete_runs") or []
    if incomplete:
        print(yellow(f"\n[!] 检测到 {len(incomplete)} 个未完成的中断批次，建议先运行 `codex2agy undo --latest` 清理:"))
        for r in incomplete:
            print(f"    - {r['run_id']} ({r['created_at']})")

    # 1. Prompts
    prompts = data["prompts"]["migratable"]
    print(bold(f"\n1. 提示词 (默认不迁): {len(prompts)} 项"))
    for i, p in enumerate(prompts, 1):
        kb = p["size_bytes"] / 1024
        warn = red(" [>24KB]") if p.get("exceeds_24kb_limit") else ""
        print(f"   [{i}] {p['name']} ({kb:.1f} KB){warn}")

    # 2. Skills
    skills = data["skills"]["migratable"]
    dup_skills = data["skills"]["skipped_duplicates"]
    print(bold(f"\n2. Skills (默认全选): {len(skills)} 个净新增") + dim(f"  |  自动跳过重复 {len(dup_skills)} 个"))
    for i, s in enumerate(skills, 1):
        print(f"   [{i}] {cyan(s['name'])} " + dim(f"({s['source_group']})"))

    # 3. Memory
    mem = data["memory"]
    sums = [m for m in mem["summary_candidates"] if not m.get("already_exists")]
    print(bold(f"\n3. 记忆 (默认不迁): {len(sums)} 份摘要 (<=24KB)") + dim("  |  Antigravity 无原生 MEMORY.md 导入接口，可转规则或只读检索 Skill"))
    for i, m in enumerate(sums, 1):
        print(f"   [{i}] {m['id']} ({m['size_bytes']/1024:.1f} KB)")

    # 4. 3rd-party plugins & MCP
    tp = data["third_party_plugins"]
    tp_skills = tp["plugin_skills_migratable"]
    tp_mcps = tp["mcps_migratable"]
    print(bold(f"\n4. 兼容第三方插件 (默认全选): {len(tp_skills)} 个 Skills + {len(tp_mcps)} 个 MCP"))
    for i, s in enumerate(tp_skills, 1):
        print(f"   [S{i}] {cyan(s['name'])} " + dim(f"({s.get('plugin_source', '')})"))
    for i, m in enumerate(tp_mcps, 1):
        print(f"   [M{i}] {green(m['name'])} " + dim(f"(MCP {m['transport']}, {m.get('plugin_source', m.get('source_kind'))})"))

    # 5. Projects (path-based: existing same-path + new Codex-only projects)
    pinfo = data.get("projects") or {}
    same_path = pinfo.get("existing_same_path") or []
    new_projs = pinfo.get("new_codex_projects") or []
    virt_projs = pinfo.get("virtual_or_scratch_projects") or []
    print(bold(f"\n5. 项目与历史会话 (按本地路径自动匹配，未归档默认迁 / 已归档默认不迁):"))
    if same_path:
        print(green(f"   已有同路径项目 ({len(same_path)} 个 · 直接合并会话):"))
        for i, p in enumerate(same_path, 1):
            root_str = (p.get("project_roots") or [""])[0]
            print(
                f"     [E{i}] {bold(p['agy_project_name'])} "
                f"未归档 {p['active_threads_count']} 条 / 已归档 {p['archived_threads_count']} 条 "
                + dim(f"({root_str}, 已迁跳过 {p['already_migrated_count']})")
            )
    if new_projs:
        print(cyan(f"   Codex 独有项目 ({len(new_projs)} 个 · 自动在 Antigravity 新建项目并同步会话):"))
        for i, p in enumerate(new_projs, 1):
            root_str = (p.get("project_roots") or [""])[0]
            print(
                f"     [N{i}] {bold(p['agy_project_name'])} "
                f"未归档 {p['active_threads_count']} 条 / 已归档 {p['archived_threads_count']} 条 "
                + dim(f"({root_str}, 已迁跳过 {p['already_migrated_count']})")
            )
    if virt_projs:
        print(dim(f"   虚拟/临时目录项目 ({len(virt_projs)} 个 · 默认跳过): " + ", ".join(p["codex_project_name"] for p in virt_projs)))
    print()


def parse_index_selection(raw: str, total: int, default_all: bool) -> List[int]:
    """Parse user input like '', 'all', 'none', '1,3', '-2' (exclude 2 from all)."""
    s = raw.strip().lower()
    if not s:
        return list(range(total)) if default_all else []
    if s in ("all", "a", "*"):
        return list(range(total))
    if s in ("none", "n", "0", "skip"):
        return []
    tokens = [t.strip() for t in s.replace(" ", ",").split(",") if t.strip()]
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


def build_plan_interactive(data: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    auto_yes = args.yes
    ls_available = bool(os.environ.get("ANTIGRAVITY_LS_ADDRESS") and os.environ.get("ANTIGRAVITY_CSRF_TOKEN"))

    # 1. Prompts (default: none)
    selected_prompts: List[Dict[str, Any]] = []
    prompts = data["prompts"]["migratable"]
    if prompts and not auto_yes:
        print(bold("\n[1/5] 提示词 (默认: 不迁移)"))
        for i, p in enumerate(prompts, 1):
            print(f"  {i}. {p['name']} ({p['size_bytes']/1024:.1f} KB) — {dim(p.get('preview', '')[:70])}")
        ans = ask("输入编号迁入 (如 1 / all)，直接回车跳过 [默认不迁]: ")
        for idx in parse_index_selection(ans, len(prompts), default_all=False):
            selected_prompts.append(prompts[idx])

    # 2. Skills (default: all)
    skills = data["skills"]["migratable"]
    selected_skills: List[Dict[str, Any]] = []
    if skills:
        if auto_yes:
            selected_skills = list(skills)
        else:
            print(bold(f"\n[2/5] Skills (默认: 全选 {len(skills)} 个；已自动跳过 {len(data['skills']['skipped_duplicates'])} 个重复项)"))
            for i, s in enumerate(skills, 1):
                print(f"  {i}. {cyan(s['name'])} ({s['source_group']})")
            ans = ask("直接回车全选，或输入编号 / `-编号` 排除 / `none` 跳过 [默认全选]: ")
            for idx in parse_index_selection(ans, len(skills), default_all=True):
                selected_skills.append(skills[idx])

    # 3. Memory (default: skip)
    selected_memory_rules: List[Dict[str, Any]] = []
    create_lookup = False
    mem_sums = [m for m in data["memory"]["summary_candidates"] if not m.get("already_exists") and m.get("fits_24kb_rule_limit")]
    if (mem_sums or data["memory"]["large_files"]) and not auto_yes:
        print(bold("\n[3/5] 记忆对比 (Codex vs Antigravity)"))
        print("  - Codex: 显式 ~/.codex/memories/ 文件")
        print("  - Antigravity: 无原生 MEMORY.md 导入接口，靠 Rules (<=24KB) 与按需 Skill 承载")
        print("  选项: 0=跳过(默认)  1=迁入摘要为按需规则  2=生成只读检索 Skill  3=两者都要")
        ans = ask("请选择 [0/1/2/3，默认 0]: ", default="0")
        if ans in ("1", "3"):
            for m in mem_sums:
                selected_memory_rules.append({
                    "source_path": m["source_path"],
                    "target_rule_name": m["target_rule_name"],
                    "trigger": "model_decision",
                })
        if ans in ("2", "3"):
            create_lookup = True

    # 4. 3rd-party plugin skills & MCP (default: all)
    tp = data["third_party_plugins"]
    tp_items: List[Dict[str, Any]] = []
    for s in tp["plugin_skills_migratable"]:
        tp_items.append({"_kind": "skill", "item": s})
    for m in tp["mcps_migratable"]:
        tp_items.append({"_kind": "mcp", "item": m})

    selected_mcps: List[Dict[str, Any]] = []
    if tp_items:
        if auto_yes:
            picked_indices = list(range(len(tp_items)))
        else:
            print(bold(f"\n[4/5] 兼容第三方插件 Skills & MCP (默认: 全选 {len(tp_items)} 项)"))
            for i, entry in enumerate(tp_items, 1):
                it = entry["item"]
                badge = cyan("Skill") if entry["_kind"] == "skill" else green("MCP  ")
                print(f"  {i}. [{badge}] {it['name']} " + dim(f"({it.get('plugin_source', it.get('source_kind', ''))})"))
            ans = ask("直接回车全选，或输入编号 / `-编号` 排除 / `none` 跳过 [默认全选]: ")
            picked_indices = parse_index_selection(ans, len(tp_items), default_all=True)
        for idx in picked_indices:
            entry = tp_items[idx]
            if entry["_kind"] == "skill":
                selected_skills.append(entry["item"])
            else:
                selected_mcps.append(entry["item"])

    # 5. Projects: existing same-path + new Codex-only projects (default ALL real local projects selected; archived default NOT selected)
    pinfo = data.get("projects") or {}
    real_projs = (pinfo.get("existing_same_path") or []) + (pinfo.get("new_codex_projects") or [])
    project_migrations: List[Dict[str, Any]] = []
    if real_projs:
        if auto_yes:
            picked_projs = [p for p in real_projs if p.get("default_selected", True)]
        else:
            print(bold(f"\n[5/5] 项目与历史会话 (默认全选以下 {len(real_projs)} 个真实本地项目，仅同步未归档会话)"))
            for i, p in enumerate(real_projs, 1):
                tag = green("同路径·直接同步") if not p.get("create_project") else cyan("Codex 独有·自动新建项目")
                root_str = (p.get("project_roots") or [""])[0]
                print(
                    f"  {i}. {bold(p['agy_project_name'])} [{tag}] "
                    f"未归档 {p['active_threads_count']} / 已归档 {p['archived_threads_count']} "
                    + dim(f"| {root_str}")
                )
            ans = ask("直接回车全选，或输入编号 / `-编号` 排除 / `none` 跳过 [默认全选]: ")
            picked_projs = [real_projs[i] for i in parse_index_selection(ans, len(real_projs), default_all=True)]

        if picked_projs:
            include_archived = args.include_archived
            if not auto_yes and any(p["archived_threads_count"] > 0 for p in picked_projs):
                total_arch = sum(p["archived_threads_count"] for p in picked_projs)
                a_ans = ask(f"选中项目共有 {total_arch} 条【已归档】会话，是否一并迁移？(迁入后仍保持已归档，零模型消耗) [y/N，默认不迁]: ", default="n").lower()
                include_archived = a_ans in ("y", "yes")

            if ls_available:
                active_mode = "transcript_and_context"
                archived_mode = "transcript_and_empty_thread"
                if not auto_yes:
                    print("  未归档会话导入方式: 1=可续聊模式(导出完整档案+侧边栏带上下文会话，默认)  2=轻量模式(仅建标题会话，零模型消耗)  3=仅导出 Markdown 档案")
                    m_ans = ask("请选择 [1/2/3，默认 1]: ", default="1")
                    active_mode = {"1": "transcript_and_context", "2": "transcript_and_empty_thread", "3": "transcript_only"}.get(m_ans, "transcript_and_context")
                    archived_mode = "transcript_only" if active_mode == "transcript_only" else "transcript_and_empty_thread"
            else:
                active_mode = "transcript_only"
                archived_mode = "transcript_only"
                print(yellow("  [提示] 当前为独立外部终端（未检测到 Antigravity LS 会话接口），项目会话将自动导出为 Markdown 档案，新建项目配置会正常写入；如需同时写入侧边栏会话请在 Antigravity 对话或内置终端内运行。"))

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


def cmd_scan(args: argparse.Namespace) -> int:
    data = run_scan(args)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_scan_summary(data)
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    data = run_scan(args)
    print_scan_summary(data)
    plan = build_plan_interactive(data, args)

    total_threads = sum(len(pm["threads"]) for pm in plan["project_migrations"])
    print(bold("\n=== 即将执行的迁移清单 ==="))
    print(f"  目标插件: {plan['target_plugin_name']}")
    print(f"  提示词: {len(plan['prompts'])}  |  Skills: {len(plan['skills'])}  |  记忆规则: {len(plan['memory_rules'])}  |  记忆检索Skill: {plan['create_memory_lookup_skill']}  |  MCP: {len(plan['mcp_servers'])}  |  项目会话: {total_threads}")
    if args.dry_run:
        print(yellow("  模式: --dry-run (演练，不写入任何文件)"))

    if not args.yes:
        confirm = ask("\n确认开始执行？(Ctrl+C 中断将默认自动撤销) [Y/n]: ", default="y").lower()
        if confirm not in ("y", "yes"):
            print("已取消，未写入任何内容。")
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
        proc = subprocess.run(cmd, text=True)
        return proc.returncode
    finally:
        if os.path.exists(plan_path):
            os.remove(plan_path)


def cmd_history(args: argparse.Namespace) -> int:
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
        print("暂无迁移记录。")
        return 0
    print(bold("\n=== codex2agy 迁移历史 ==="))
    for r in runs:
        print(
            f"  {cyan(r['run_id'])}  |  status={bold(r['status'])}  |  "
            f"plugin={r['target_plugin_name']}  |  actions={r['action_count']}  |  "
            f"sessions={r['synced_session_count']}  |  {dim(r['created_at'])}"
        )
    print()
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
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
        target = args.run_id or "最近一次迁移"
        confirm = ask(f"确认撤回 {target}？（只删除该批次新建项，不动原有配置）[y/N]: ", default="n").lower()
        if confirm not in ("y", "yes"):
            print("已取消撤回。")
            return 0
    return subprocess.run(cmd).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex2agy",
        description="Safe, interactive, transactional Codex -> Antigravity migrator CLI",
    )
    parser.add_argument("--codex-home", default=None, help="Override ~/.codex path")
    parser.add_argument("--gemini-config", default=None, help="Override ~/.gemini/config path")
    parser.add_argument("--ledger-dir", default=None, help="Override idempotency ledger path")
    parser.add_argument("--target-plugin", default="codex-imported", help="Target plugin name (default: codex-imported)")

    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="Read-only scan of Codex assets & matching projects")
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
    cmd = args.command or "migrate"
    if cmd == "scan":
        return cmd_scan(args)
    if cmd == "migrate":
        if not hasattr(args, "dry_run"):
            args.dry_run = False
            args.yes = False
            args.include_archived = False
        return cmd_migrate(args)
    if cmd == "history":
        return cmd_history(args)
    if cmd == "undo":
        return cmd_undo(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
