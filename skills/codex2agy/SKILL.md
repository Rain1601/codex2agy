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

### Recommended `ask_question` Card Layout (Single Call, Up to 4 Crisp Questions)

Only include a question if that category has discovered migratable items:

1. **Q1 — Prompts (`is_multi_select: false`)** *(only if prompts/AGENTS.md found)*:
   - ZH Question: `提示词（AGENTS.md 等）是否迁移？`
     - Options: `(Recommended) 不迁移（跳过）`, `迁移为 Antigravity 常驻规则`
   - EN Question: `Migrate Codex prompts (AGENTS.md)?`
     - Options: `(Recommended) Skip prompts`, `Migrate as always-on Antigravity rule`
2. **Q2 — Memory (`is_multi_select: false`)** *(only if memory summaries/files found)*:
   - ZH Question: `记忆（Memory）如何处理？（Antigravity 无原生 MEMORY.md 导入接口）`
     - Options:
       - `(Recommended) 不迁移（跳过）`
       - `迁入精炼摘要（转为 ≤24KB 按需加载规则）`
       - `生成只读检索 Skill（不复制大文件，按需读取 ~/.codex/memories/）`
       - `两者都要（摘要规则 + 只读检索 Skill）`
   - EN Question: `How should Codex Memory be handled? (No native MEMORY.md import in Antigravity)`
     - Options:
       - `(Recommended) Skip memory`
       - `Migrate compact summaries as on-demand Rules (<=24KB)`
       - `Create read-only lookup Skill (reads ~/.codex/memories/ on demand)`
       - `Both summary Rules and lookup Skill`
3. **Q3 — Skills, 3rd-Party Plugins & Projects (`is_multi_select: true`)**:
   - Let the user click-select all high-level bundles/projects they want synced, putting the full recommended bundle first or listing each clean group so it's effortless:
   - ZH Question: `选择要同步的模块与项目`
     - Options (list discovered groups clearly):
       - `(Recommended) 净新增 Skills（N 个，已跳过重复）`
       - `兼容第三方插件 Skills & MCP（M 项）`
       - `已有同路径项目：<name1>、<name2>（直接合并未归档会话）`
       - `Codex 独有项目：<name3>、<name4>（自动在 Antigravity 新建项目并同步未归档会话）`
       *(If there are <= 4 projects total, you may also list each project as its own checkbox option for finer control.)*
   - EN Question: `Select categories and projects to sync`
     - Options matching the discovered items above in plain English.
4. **Q4 — Archived Project Sessions (`is_multi_select: false`)** *(only if selected/discovered projects have `archived_threads_count > 0`)*:
   - ZH Question: `项目的「已归档」历史会话是否一并迁移？（共 K 条）`
     - Options:
       - `(Recommended) 不迁移已归档会话（仅迁未归档，默认可续聊模式）`
       - `一并迁移已归档会话（保持已归档状态，零模型消耗）`
       - `仅演练预览（--dry-run，不写入任何文件）`
   - EN Question: `Include archived project sessions too? (K total)`
     - Options:
       - `(Recommended) Skip archived sessions (sync active sessions only, continuable mode)`
       - `Include archived sessions (preserve archived state, zero model calls)`
       - `Dry-run preview only (write nothing)`

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
