---
name: codex2agy
description: >-
  Interactively discover, compare, and safely migrate Codex prompts (AGENTS.md and prompts/*.md),
  skills, memory summaries, compatible local third-party Codex plugins/MCP servers, and projects
  + sessions (same-path projects and Codex-only local projects, active + optional archived) into
  Antigravity. Supports strict session idempotency, interrupt auto-rollback, and batch undo.
---

# codex2agy — Safe, Transactional Codex-to-Antigravity Migrator

Migrate user assets from `~/.codex/` (and `~/.agents/skills/`) into the dedicated Antigravity plugin `~/.gemini/config/plugins/codex-imported/` (default; **do NOT ask the user to confirm the target plugin name** unless they explicitly ask to customize it), and sync projects + conversations via `ConversationService`.

## Hard Guarantees (Enforced in Code)

1. **Read-only on source**: Never edit, move, or delete anything under `~/.codex/` or `~/.agents/`.
2. **Never overwrite**: Existing Skills, Rules, MCP servers, Projects, transcripts, or conversations are automatically skipped.
3. **Strict Session Idempotency (数据幂等)**:
   - Backed by a zero-dependency, human-readable atomic JSON ledger (`~/.gemini/antigravity/plugin_data/codex2agy/migration_ledger.json`, atomic `tmp + fsync + os.replace`), a cross-platform `O_CREAT|O_EXCL` exclusive lockfile, plus live Antigravity `codex-thread:<id>` tag & disk checks.
   - The same Codex session can NEVER be synced more than once.
4. **Interrupt Auto-Rollback (中断默认撤销)**:
   - Receiving `SIGINT` / `SIGTERM` or hitting a mid-run failure automatically rolls back every file, newly created Antigravity project JSON, MCP entry, ledger reservation, and conversation created in that run.
5. **Reversible Undo (撤回/撤销)**:
   - Every run has a `run_id` and can be cleanly undone via `rollback_migration.py`.
6. **Never write before confirmation / Never echo secrets**.

---

## Step 1: Discover (Read-Only Scan)

Run the bundled scanner script relative to this skill directory (typically `~/.gemini/config/plugins/codex2agy/skills/codex2agy/scripts/scan_codex.py`):

```bash
python3 <skill_dir>/scripts/scan_codex.py --pretty
```

If `incomplete_runs` is non-empty, alert the user and offer to roll back that incomplete `run_id` first.

---

## Step 2: Present Findings & Collect User Confirmation (Bilingual EN/ZH, Self-Explanatory)

**UX Rules for presenting Step 2 (CRITICAL):**
- **Match the user's language automatically** (respond in Chinese if the user wrote Chinese; respond in English if the user wrote English).
- **Do NOT ask the user to confirm the target plugin name** (`codex-imported` is automatic; only mention in passing that migrated items go into an isolated reversible plugin).
- **Never use cryptic option numbers like "pick 2 / 3 / 4" without spelling out what each choice does in plain words.** Every selectable option must be self-explanatory at a glance.
- **Keep the confirmation concise and scannable** so the user can either reply `按默认执行` / `Proceed with defaults` in one second or state specific adjustments clearly.

Present these **5 sections** with their exact defaults:

### 1. 提示词（`AGENTS.md` / `prompts/*.md`）— 默认：**不迁移**
- Show file name, size, and short preview (warn if > 24 KB).
- State clearly: **默认不迁移**（如需迁入为 Antigravity 规则，说明即可）。

### 2. Skills — 默认：**全选净新增项**
- List all net-new skills (name + source) and mention how many duplicate skills were automatically skipped.

### 3. 记忆（Memory）— 默认：**不迁移**
Show a concise 2-line comparison of Codex (`~/.codex/memories/`) vs Antigravity (no native `MEMORY.md` import; relies on Rules ≤ 24 KB + on-demand Skills), list discovered summary sizes, and write out the **plain-language options** directly:
- **不迁移（默认）**
- **迁入精炼摘要**：把 ≤ 24 KB 的 `memory_summary.md` 转为按需触发的规则
- **生成只读检索 Skill**：不复制大文件，需要查历史上下文时按需读取 `~/.codex/memories/`
- **两者都要**

### 4. 兼容第三方插件（Skills + MCP）— 默认：**全选净新增项**
- List compatible third-party plugin skills and MCP servers (skipping duplicates and Codex built-in runtimes like `node_repl` / `openai-*`).

### 5. 项目与历史会话（按真实本地路径自动匹配）— 默认：**同步所有真实本地项目的未归档会话；已归档默认不迁**
Use pure path-based matching — no confusing "same-name different-path" guesswork:
- **已有同路径项目（直接同步会话）**：`projects.existing_same_path` — Codex 与 Antigravity 指向同一个本地目录，直接把会话同步进对应已有项目。列出：项目名、路径、未归档数（默认迁）、已归档数（默认不迁）。
- **Codex 独有项目（自动在 Antigravity 新建项目并同步会话）**：`projects.new_codex_projects` — Codex 中有真实本地目录但 Antigravity 里还没有的项目，自动新建对应项目并同步会话（如遇重名会自动附加目录名区分）。列出：新项目名、路径、未归档数（默认迁）、已归档数（默认不迁）。
- **虚拟 / 临时目录项目（默认跳过）**：`projects.virtual_or_scratch_projects`（如 `.chatgpt-projects` / `scratch-workspaces`），仅一行带过，默认不选。
- **默认导入方式（无需用户费心选择，直接说明即可）**：
  - 未归档会话默认 **可续聊模式**（导出完整 Markdown 档案 + 侧边栏创建带上下文交接的会话，点开即可接着聊；也可改选“轻量模式：零模型消耗仅建标题”或“仅导出 Markdown 档案”）。
  - 若用户额外勾选 **已归档会话**，默认使用 **轻量归档模式**（导出完整 Markdown 档案 + 侧边栏建标题会话并保持 `archived: true`，零模型消耗）。

### Confirmation Prompt Templates (Use Matching Language)

**Chinese template (中文用户):**
> **请确认是否开始迁移**（直接回复 **`按默认执行`** 即可，或说明想调整的项）：
> - **提示词（`AGENTS.md`）**：默认 **不迁移**（要迁请说“迁提示词”）
> - **Skills（N 个）& 第三方插件/MCP（M 项）**：默认 **全选**（有要排除的请点名）
> - **记忆（Memory）**：默认 **不迁移**（可选：**迁入精炼摘要** / **生成只读检索 Skill** / **两者都要**）
> - **项目与会话**：默认同步上述 **同路径已有项目 + Codex 独有项目（自动新建）** 的 **未归档会话（可续聊模式）**；**已归档会话默认不迁**（要一起迁请说“包含已归档”，或点名只迁哪几个项目）

**English template (English users):**
> **Confirm Migration Plan** (reply **`Proceed with defaults`** to start, or mention any adjustments):
> - **Prompts (`AGENTS.md`)**: Default **skip** (say "include prompts" to migrate)
> - **Skills (N) & 3rd-Party Plugins/MCP (M)**: Default **all selected** (name any you want excluded; duplicates auto-skipped)
> - **Memory**: Default **skip** (options: **migrate summary rules** / **create read-only lookup skill** / **both**)
> - **Projects & Sessions**: Default sync **active (unarchived) sessions** for all listed **same-path existing projects + Codex-only local projects (auto-created in Antigravity)** using **continuable context mode**; **archived sessions default to skip** (say "include archived" to import them in lightweight archived mode)

Wait for explicit user confirmation before Step 3.

---

## Step 3: Apply Transactional Migration

Write the confirmed plan JSON to `/tmp/codex2agy-plan.json` (`target_plugin_name` defaults to `"codex-imported"`). For each selected project in `project_migrations`, pass:

```json
{
  "agy_project_id": "<existing-id-or-null>",
  "agy_project_name": "<project-display-name>",
  "create_project": false,
  "project_roots": ["/abs/path/to/repo"],
  "git_default_branch": "main",
  "conversation_mode": "transcript_and_context",
  "archived_conversation_mode": "transcript_and_empty_thread",
  "threads": [...]
}
```

Execute:

```bash
python3 <skill_dir>/scripts/apply_migration.py --plan /tmp/codex2agy-plan.json
```

---

## Step 4: Verify & Hand Back

Report concisely:
- `run_id` and `status`
- Created items (including newly created Antigravity projects, per-project active/archived conversations, skills, rules, MCPs)
- Skipped duplicates (`skipped_existing` & `skipped_idempotent`)
- Direct link to destination plugin: `[codex-imported](customization://codex-imported)`
- Remind the user they can say **"撤回刚才的迁移"** at any time.

---

## Undo / Rollback Workflow

When the user asks to undo/rollback a migration:

```bash
python3 <skill_dir>/scripts/rollback_migration.py --list
python3 <skill_dir>/scripts/rollback_migration.py --latest
# or --run-id <RUN_ID>
```

Rolling back removes all newly created files, newly created Antigravity project JSONs, added MCP entries, and created Antigravity conversations from that run, and releases those session IDs from the idempotency ledger.
