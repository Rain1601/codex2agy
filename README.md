<p align="center">
  <img src="./assets/logo.png" alt="codex2agy logo" width="128" height="128" />
</p>

<h1 align="center">codex2agy</h1>

<p align="center">
  <strong>English</strong> · <a href="./README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  Safely, incrementally, and reversibly migrate your local <strong>Codex</strong> setup into <strong>Antigravity</strong>.<br/>
  <strong>One-sentence Antigravity auto-install &amp; migrate · Interactive Terminal CLI</strong> · Zero dependencies (Python 3.9+) · macOS / Linux / Windows
</p>

---

## 1. Easiest Way: Just Paste to Antigravity (Recommended)

No terminal commands needed — send this single message directly to **Antigravity**:

```text
Please install the Antigravity plugin from https://github.com/Rain1601/codex2agy and immediately scan and migrate my local Codex setup.
```

> **Instructions for Antigravity Agent (auto-run upon reading this page):**
> 1. **Install plugin**: Clone `https://github.com/Rain1601/codex2agy.git` into `~/.gemini/config/plugins/codex2agy` (`%USERPROFILE%\.gemini\config\plugins\codex2agy` on Windows). If it already exists as a git repo, run `git pull`.
> 2. **Optional CLI symlink (macOS/Linux)**: If `~/.local/bin` exists or can be created, run `chmod +x ~/.gemini/config/plugins/codex2agy/bin/codex2agy && ln -sf ~/.gemini/config/plugins/codex2agy/bin/codex2agy ~/.local/bin/codex2agy`.
> 3. **Start migration immediately (no restart needed)**: Read `~/.gemini/config/plugins/codex2agy/skills/codex2agy/SKILL.md` and run Step 1 (read-only scan) → Step 2 (present the 5 clear categories in the user's language and wait for confirmation; **never write before confirmation**) → Step 3 (transactional migration).

---

## 2. Manual Installation

### Option 1: One-Line Git Clone (Recommended)

**macOS / Linux:**

```bash
git clone https://github.com/Rain1601/codex2agy.git ~/.gemini/config/plugins/codex2agy
```

*(Optional)* Register the global `codex2agy` terminal command:

```bash
mkdir -p ~/.local/bin && ln -sf ~/.gemini/config/plugins/codex2agy/bin/codex2agy ~/.local/bin/codex2agy
```

**Windows (PowerShell):**

```powershell
git clone https://github.com/Rain1601/codex2agy.git "$env:USERPROFILE\.gemini\config\plugins\codex2agy"
```

### Option 2: Download ZIP (No Terminal)

1. Click **Code → Download ZIP** on this repository.
2. Unzip and rename the folder to `codex2agy`, then place it at:
   - macOS / Linux: `~/.gemini/config/plugins/codex2agy/`
   - Windows: `%USERPROFILE%\.gemini\config\plugins\codex2agy\`

### Option 3: Antigravity CLI

```bash
git clone https://github.com/Rain1601/codex2agy.git
agy plugin install ./codex2agy
```

- **Update**: `cd ~/.gemini/config/plugins/codex2agy && git pull`
- **Uninstall**: Uninstall from Antigravity's Plugins page, or delete `~/.gemini/config/plugins/codex2agy`

---

## 3. Daily Usage (Chat or Terminal)

### Mode A: Chat Trigger (Inside Antigravity)

In any Antigravity chat, simply say:

- `Use codex2agy to scan and migrate my Codex setup`
- `Sync my missing Codex skills and project sessions to Antigravity`
- `Undo my last codex2agy migration`

### Mode B: Terminal CLI (`--lang auto|en|zh`)

```bash
# 1. Read-only scan (preview all 5 categories)
codex2agy scan
codex2agy --lang en scan

# 2. Interactive migration wizard (Enter accepts defaults; Ctrl+C auto-rolls back)
codex2agy migrate
codex2agy migrate --dry-run

# 3. View past migration runs
codex2agy history

# 4. Rollback latest or specific migration run
codex2agy undo
codex2agy undo --run-id <RUN_ID> --dry-run
```

> Note: Running `codex2agy migrate` in an external terminal exports project sessions as read-only Markdown transcripts and creates project configs. To also populate Antigravity's sidebar conversation list directly, run inside Antigravity chat or Antigravity's built-in terminal.

---

## 4. What Gets Migrated? (Defaults at a Glance)

| Category | Scope | Default | Notes |
| :--- | :--- | :---: | :--- |
| **1. Prompts** | `AGENTS.md`, `prompts/*.md` | **Skip** | Preview shown; migrated only if you opt in |
| **2. Skills** | Codex / `~/.agents` / memory skills | **Select all** | Existing Antigravity skills with the same name are auto-skipped |
| **3. Memory** | `memory_summary.md` & memory folders | **Skip** | Compares Codex vs Antigravity first; opt into summary rules or a read-only lookup skill |
| **4. 3rd-Party Plugins & MCP** | Compatible plugin skills & stdio/HTTP MCPs | **Select all** | Auto-skips Codex internal runtimes (`node_repl`, `openai-*`) |
| **5. Projects & Sessions** | Same-path projects + Codex-only local projects (auto-created) | **Active: sync / Archived: skip** | Path-based matching; archived sessions are optional and preserve `archived: true` when imported |

---

## 5. Safety & Transactional Guarantees

- **Read-Only Source**: Never modifies, moves, or deletes anything under `~/.codex` or `~/.agents`.
- **No Duplicates, Never Overwrites**: Existing skills, rules, MCP servers, projects, and sessions are automatically skipped.
- **Strict Session Idempotency**: Powered by a zero-dependency atomic JSON ledger (`~/.gemini/antigravity/plugin_data/codex2agy/migration_ledger.json`), a cross-platform exclusive lockfile, and Antigravity session tags — **the same Codex session can never be synced twice**, even on retries or across different target plugins.
- **Interrupt Auto-Rollback**: `Ctrl+C` (`SIGINT`/`SIGTERM`) or any mid-run error automatically rolls back every created file, project JSON, MCP entry, and conversation — zero partial state.
- **Batch Undo & Isolation**: Migrated assets live inside a single isolated plugin (`codex-imported`) and every run can be cleanly undone at any time without touching pre-existing configs.
