---
name: codex2agy
description: >-
  Interactively discover, compare, and safely migrate Codex prompts (AGENTS.md and prompts/*.md),
  skills, memory summaries, compatible local third-party Codex plugins/MCP servers, and projects
  + sessions (same-path projects and Codex-only local projects, active + optional archived) into
  Antigravity. Uses native option cards (ask_question), live step progress (update_steps), and
  inline rich result dashboards, with strict session idempotency, interrupt auto-rollback, and undo.
---

# codex2agy — Safe, Transactional Codex-to-Antigravity Migrator

Migrate user assets from `~/.codex/` (and `~/.agents/skills/`) into the dedicated Antigravity plugin `~/.gemini/config/plugins/codex-imported/` (**never ask the user to confirm the target plugin name** unless they explicitly ask to rename it), and sync projects + conversations via `ConversationService`.

## Post-Install & First-Run Auto-Start (CRITICAL)

If the user just installed `codex2agy` (for example via an AgentBuddy install prompt saying *"安装完成后基于该 skill 的元数据协助我完成后续配置"* / *"help me configure after installation"*):
- **Step 1 (`scan_codex.py`) is 100% read-only and makes zero environment changes.**
- **NEVER stop after reading `SKILL.md` to ask in plain text *"Should I run the read-only scan now?"*.**
- Immediately run Step 1 (`scan_codex.py --pretty`) using the scripts located next to this `SKILL.md`, and immediately present Step 2 using the native **`ask_question`** 5-tab option cards so the user gets an instant interactive approval flow before any write happens.

## Hard Guarantees (Enforced in Code)

1. **Read-only on source**: Never edit, move, or delete anything under `~/.codex/` or `~/.agents/`.
2. **Never overwrite**: Existing Skills, Rules, MCP servers, Projects, transcripts, or conversations are automatically skipped.
3. **Strict Session Idempotency (数据幂等)**: Zero-dependency atomic JSON ledger (`~/.gemini/antigravity/plugin_data/codex2agy/migration_ledger.json`) + cross-platform exclusive lock + Antigravity `codex-thread:<id>` tags guarantee the same Codex session can NEVER be synced twice.
4. **Interrupt Auto-Rollback (中断默认撤销)**: `SIGINT` / `SIGTERM` or any mid-run error automatically rolls back all created files, newly created project JSONs, MCP entries, ledger reservations, and conversations.
5. **Reversible Undo (撤回/撤销)**: Every run has a `run_id` and can be undone cleanly via `rollback_migration.py`.
6. **Never write before confirmation / Never echo secrets / Match the user's language (EN or ZH)**.

---

## Live Progress UX (MUST DO Throughout the Run)

To keep the user informed at every step instead of waiting blindly:
1. **Start of Step 1**: Call `update_steps` with a 4-step live checklist (in the user's language), marking step 1 `in_progress`, and call `update_focus`:
   - Step 1: Scan local Codex setup (只读扫描本机 Codex 内容)
   - Step 2: Confirm selections via option cards (确认迁移选项)
   - Step 3: Run transactional migration (执行增量同步)
   - Step 4: Verify & render result summary (校验并渲染同步结果)
2. **Every stage transition**: Update `update_steps` (mark finished step `done`, next step `in_progress`) and `update_focus`, and include a 1–2 sentence status update telling the user what finished and what's next.
3. During Step 3, `apply_migration.py` automatically streams real-time per-item progress (`[codex2agy][sessions] (3/12 · 25%) ...`) to `stderr` while keeping `stdout` clean JSON.

---

## Step 1: Discover (Read-Only Scan)

Call `update_steps` + `update_focus`, then run the bundled scanner relative to this skill directory:

```bash
python3 <skill_dir>/scripts/scan_codex.py --pretty
```

If `incomplete_runs` is non-empty, alert the user and offer to roll back that incomplete `run_id` first.

---

## Step 2: Present Concise Scan Summary & Confirm via Native Option Cards (`ask_question`)

**CRITICAL UX Rules:**
- **Match the user's language** (Chinese or English).
- **Do NOT ask for the target plugin name** (`codex-imported` is automatic).
- **Always use the native `ask_question` tool** to render clickable option cards rather than asking the user to type text answers or decode numbers like "2 / 3 / 4".
- First output a **compact, scannable summary** of the 5 scanned categories (counts + names, showing Codex vs Antigravity memory comparison in 2 lines, and distinguishing `existing_same_path` vs `new_codex_projects` by real local path).
- **In the same turn**, call **`ask_question`** with clickable cards!

### Recommended `ask_question` Card Layout (5 Clean Single-Topic Tabs)

**CRITICAL UX Rule**: Keep **one clear topic per card tab** — **NEVER mix Skills, same-path projects, and Codex-only projects into one crowded Multi-select tab.** Every tab is a crisp single-select (`is_multi_select: false`) whose first option is `(Recommended)` so the user can accept defaults effortlessly or customize each category independently.

Only include a tab if that category has discovered items:

1. **Tab 1 — Prompts (`is_multi_select: false`)** *(only if `AGENTS.md` / `prompts/*.md` found)*:
   - ZH: `提示词（AGENTS.md 等）是否迁移？`
     - `(Recommended) 不迁移（跳过）`
     - `迁移为 Antigravity 常驻规则`
   - EN: `Migrate Codex prompts (AGENTS.md)?`
     - `(Recommended) Skip prompts`
     - `Migrate as always-on Antigravity rule`

2. **Tab 2 — Skills & Compatible 3rd-Party Plugins/MCP (`is_multi_select: false`)** *(only if net-new skills or plugins/MCP found)*:
   - ZH: `Skills 与兼容第三方插件/MCP（已自动排除重复项）`
     - `(Recommended) 全部同步（N 个净新增 Skills + M 项插件 Skills/MCP）`
     - `仅同步净新增 Skills（N 个）`
     - `仅同步兼容第三方插件 Skills/MCP（M 项）`
     - `跳过此项`
   - EN: `Skills & Compatible 3rd-Party Plugins/MCP (duplicates already excluded)`
     - `(Recommended) Sync all (N net-new Skills + M plugin Skills/MCP)`
     - `Sync net-new Skills only (N)`
     - `Sync compatible 3rd-party plugin Skills/MCP only (M)`
     - `Skip`

3. **Tab 3 — Memory (`is_multi_select: false`)** *(only if Codex memory files found — write natural, outcome-first labels so the user knows what to pick at a glance)*:
   - ZH: `记忆（Memory）想怎么处理？`
     - `(Recommended) 不迁移 — 想从零开始、保持最干净，以后用 /learn 现教`
     - `迁入精炼摘要 — 想让我一上来就懂你的习惯（聊到相关事情时自动生效）`
     - `只读检索 Skill — 平时不打扰，偶尔想翻“以前在 Codex 怎么弄的”再去查`
     - `两者都要 — 既延续习惯又不丢历史，嫌烦随时可在插件页一键关掉`
   - EN: `How should we handle your Codex memory?`
     - `(Recommended) Skip — start fresh and clean, teach habits later with /learn`
     - `Import compact summaries — pick up your habits right away when relevant`
     - `Read-only lookup Skill — stay out of the way unless you ask to check old Codex notes`
     - `Both — keep habits and searchable history (can toggle off anytime in the plugin)`

4. **Tab 4 — Same-Path Existing Projects (`is_multi_select: false`)** *(dedicated tab! Only if `projects.existing_same_path` is non-empty)*:
   - Default behavior: **import new (unmigrated) active sessions** into the matching existing Antigravity projects.
   - ZH: `同路径已有项目（<name1>、<name2>）：是否导入新会话？`
     - `(Recommended) 全部导入新会话（共 A 条未归档新会话，默认可续聊模式）`
     - `全部导入新会话 + 已归档会话（含 B 条已归档，保持归档态、零模型消耗）`
     - `自定义选择其中部分同路径项目`
     - `跳过同路径项目`
   - EN: `Same-path existing projects (<name1>, <name2>): import new sessions?`
     - `(Recommended) Import all new active sessions (A sessions, continuable mode)`
     - `Import all new active + archived sessions (incl. B archived, zero model calls)`
     - `Pick specific same-path projects`
     - `Skip same-path projects`

5. **Tab 5 — Codex-Only Projects (`is_multi_select: false`)** *(dedicated tab! Only if `projects.new_codex_projects` is non-empty)*:
   - Default behavior: **auto-create all discovered local Codex-only projects in Antigravity and import their sessions**.
   - ZH: `Codex 独有本地项目（<name3>、<name4>）：是否新建项目并导入？`
     - `(Recommended) 全部新建项目并导入未归档会话（共 C 条，默认可续聊模式）`
     - `全部新建项目并导入全部会话（含 D 条已归档，保持归档态、零模型消耗）`
     - `自定义选择其中部分独有项目`
     - `跳过 Codex 独有项目`
   - EN: `Codex-only local projects (<name3>, <name4>): create projects & import?`
     - `(Recommended) Create all & import active sessions (C sessions, continuable mode)`
     - `Create all & import active + archived sessions (incl. D archived, zero model calls)`
     - `Pick specific Codex-only projects`
     - `Skip Codex-only projects`

*(Only if the user chooses "自定义选择其中部分..." / "Pick specific..." in Tab 4 or Tab 5, send one focused follow-up `ask_question` with `is_multi_select: true` listing only those projects.)*

Wait for the user's card selections before Step 3.

---

## Step 3: Apply Transactional Migration (With Live Progress)

1. Call `update_steps` (Step 2 `done`, Step 3 `in_progress`) and `update_focus`. Tell the user briefly what is being synced right now (e.g., counts of skills, MCPs, projects, and sessions).
2. Write the confirmed plan JSON to `/tmp/codex2agy-plan.json` (`target_plugin_name: "codex-imported"`).
3. Run:

```bash
python3 <skill_dir>/scripts/apply_migration.py --plan /tmp/codex2agy-plan.json
```

`apply_migration.py` prints live progress to `stderr` and the final structured JSON result to `stdout`. On any interrupt or failure, it automatically rolls back and reports `interrupted_rolled_back` / `failed_rolled_back`.

---

## Step 4: Verify & Render Inline Visual Result Card

1. Call `update_steps` (Step 3 `done`, Step 4 `in_progress`) and `update_focus`.
2. **Render a rich inline HTML Result Dashboard** using the bundled template `<skill_dir>/references/result_card_template.html` (following the `generative_ui` skill rules):
   - Read `<skill_dir>/references/result_card_template.html`.
   - Substitute `__RESULT_JSON__` with the exact JSON result object returned by `apply_migration.py` and `__LANG__` with `"zh"` or `"en"`.
   - Write the rendered HTML to `<Artifact Directory Path>/codex2agy-result.html` using the native `write_file` tool with `ArtifactMetadata: {"UserFacing": true, "RequestFeedback": false, "Summary": "codex2agy migration result dashboard"}`.
   - Embed it inline in your final response:
     ```markdown
     <agent-embed src="file:///<Artifact Directory Path>/codex2agy-result.html"></agent-embed>
     ```
3. Mark all `update_steps` as `done`.
4. Alongside the inline dashboard, add only 2–3 concise lines:
   - Direct link to the destination plugin: `[codex-imported](customization://codex-imported)`
   - Any required MCP `variables` notice (if non-empty, remind the user to fill them in and toggle the plugin on)
   - One-line reminder that they can say **"撤回刚才的迁移"** / **"Undo my last migration"** anytime.

---

## Undo / Rollback Workflow

When the user asks to undo/rollback a migration:

```bash
python3 <skill_dir>/scripts/rollback_migration.py --list
python3 <skill_dir>/scripts/rollback_migration.py --latest
# or --run-id <RUN_ID>
```

Confirm which run to revert via `ask_question` if multiple runs exist, run the rollback, and report reverted items + released session IDs.
