---
name: codex2agy
description: >-
  Interactively discover, compare, and safely migrate Codex prompts (AGENTS.md and prompts/*.md),
  skills, memory summaries, compatible local third-party Codex plugins/MCP servers, and matching
  same-project conversations (active + optional archived) into Antigravity. Supports strict
  session idempotency (never syncs a session twice), interrupt auto-rollback, and batch undo.
---

# codex2agy — Safe, Transactional Codex-to-Antigravity Migrator

Migrate user assets from `~/.codex/` (and `~/.agents/skills/`) into **one dedicated Antigravity plugin** (default: `~/.gemini/config/plugins/codex-imported/`), and sync matching same-project conversations into the corresponding Antigravity Projects via `ConversationService`.

## Hard Guarantees (Enforced in Code)

1. **Read-only on source**: Never edit, move, or delete anything under `~/.codex/` or `~/.agents/`.
2. **Never overwrite**: Existing Skills, Rules, MCP servers, transcripts, or conversations are automatically skipped.
3. **Strict Session Idempotency (数据幂等)**:
   - Backed by a zero-dependency, human-readable atomic JSON ledger (`~/.gemini/antigravity/plugin_data/codex2agy/migration_ledger.json`, atomic `tmp + fsync + os.replace`), a cross-platform `O_CREAT|O_EXCL` exclusive lockfile (works on macOS/Linux/Windows with stale-PID recovery), plus live Antigravity `codex-thread:<id>` tag & disk checks.
   - Even if the user accidentally triggers sync twice, runs concurrently, or changes the target plugin name, **the same Codex session can NEVER be synced more than once**.
4. **Interrupt Auto-Rollback (中断默认撤销)**:
   - Receiving `SIGINT` / `SIGTERM` or hitting a mid-run failure **automatically rolls back** every file, MCP entry, ledger reservation, and Antigravity conversation created in that run, leaving zero partial/dirty state.
5. **Reversible Undo (撤回/撤销)**:
   - Every committed or interrupted run has a `run_id` recorded in the ledger and can be undone at any time via `rollback_migration.py` (only removes resources created by that run; never touches pre-existing user files; releases rolled-back session IDs so they can be re-synced cleanly later if desired).
6. **Never write before confirmation / Never echo secrets**.

---

## Step 1: Discover (Read-Only Scan)

Run the bundled scanner script relative to this skill directory (e.g., `./scripts/scan_codex.py`, typically `~/.gemini/config/plugins/codex2agy/skills/codex2agy/scripts/scan_codex.py`):

```bash
python3 <skill_dir>/scripts/scan_codex.py --pretty
```

If `incomplete_runs` is non-empty (e.g., a prior process was hard-killed with `SIGKILL`), alert the user immediately and offer to roll back that incomplete `run_id` first using `rollback_migration.py --run-id <RUN_ID>`.

---

## Step 2: Present Findings & Collect User Selections

Present the scan results in **5 structured sections** matching these exact defaults:

### Section 1 — Prompts & Instructions (`default: DO NOT migrate`)
- Show discovered `~/.codex/AGENTS.md` (size, headings/preview, warn if > 24 KB) and `~/.codex/prompts/*.md`.
- Explicitly mark **default = Do not migrate (不迁移)**.

### Section 2 — Skills (`default: ALL non-duplicate selected`)
- List net-new skills from `~/.codex/skills/` (excluding `.system/`), `~/.agents/skills/`, and `~/.codex/memories/skills/`.
- Mark **default = ALL selected**, and report skipped duplicates separately.

### Section 3 — Memory (`show Codex vs Antigravity comparison first`)
Display the comparison table (Codex explicit `~/.codex/memories/` vs Antigravity Rules ≤ 24 KB / 20k tokens budget & on-demand Skills, no native `MEMORY.md` import API), show file sizes, and offer options (**default: skip**):
1. Skip memory (default)
2. Migrate compact `memory_summary.md` (`<= 24 KB`) as plugin Rules (`model_decision` recommended, or `always_on`)
3. Create a read-only `codex-memory-lookup` skill
4. Both (2 + 3)

### Section 4 — Compatible Local 3rd-Party Plugins & MCP (`default: ALL compatible non-duplicate selected`)
- List compatible plugin skills and stdio/Streamable-HTTP MCP servers as selectable options (**default: ALL selected**), skipping duplicates and built-in Codex runtime plugins (`openai-*`, `node_repl`, `hooks.json`, `rules/default.rules`, legacy SSE).

### Section 5 — Matching Projects & Conversations (`same-project content sync`)
- Show matched Antigravity Projects:
  - **`exact_root` (强匹配 · 同目录，默认勾选)**
  - **`same_name` (同名不同路径/未绑定目录，展示两端路径由用户确认)**
- For each matched project, separate:
  - **Active (unarchived) threads** (`default: selected` once project is confirmed)
  - **Archived threads** (**`default: DO NOT migrate — optional, user explicitly chooses`**; migrated archived threads automatically preserve `archived: true` in Antigravity)
  - **Already-migrated threads** (automatically skipped via idempotency ledger + tags)
- Let user pick import mode (`transcript_and_context`, `transcript_and_empty_thread`, or `transcript_only`; recommend zero-model modes for batches > 20 threads).

Ask once for all confirmations + destination plugin name (default `codex-imported`) and wait before Step 3.

---

## Step 3: Apply Transactional Migration

Write the confirmed plan JSON to `/tmp/codex2agy-plan.json` and execute:

```bash
python3 ~/.gemini/config/plugins/codex2agy/skills/codex2agy/scripts/apply_migration.py --plan /tmp/codex2agy-plan.json
```

- Interrupting mid-run (`SIGINT` / `SIGTERM`) or encountering an error automatically reverts everything created during that run and reports `status: "interrupted_rolled_back"` / `"failed_rolled_back"`.
- Always record and display the returned `run_id` to the user so they can undo later if needed.

---

## Step 4: Verify & Hand Back

Report concisely:
- `run_id` and `status`
- Created items (by category and per-Project active/archived counts)
- Skipped duplicates (`skipped_existing` & `skipped_idempotent`)
- Link to destination plugin: `[<target-plugin-name>](customization://<target-plugin-name>)`
- Remind the user they can say **"撤回刚才的迁移"** at any time.

---

## Undo / Rollback Workflow

When the user asks to undo/rollback a migration (or inspect past runs):

1. List runs:
   ```bash
   python3 ~/.gemini/config/plugins/codex2agy/skills/codex2agy/scripts/rollback_migration.py --list
   ```
2. Preview rollback (optional `--dry-run`) and confirm which `run_id` to revert, then execute:
   ```bash
   python3 ~/.gemini/config/plugins/codex2agy/skills/codex2agy/scripts/rollback_migration.py --latest
   # or:
   python3 ~/.gemini/config/plugins/codex2agy/skills/codex2agy/scripts/rollback_migration.py --run-id <RUN_ID>
   ```
3. Report every reverted file, MCP entry, and deleted Antigravity conversation, and confirm that the rolled-back Codex session IDs have been released from the idempotency ledger.
