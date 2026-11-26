<p align="center">
  <img src="./assets/logo.png" alt="codex2agy logo" width="128" height="128" />
</p>

<h1 align="center">codex2agy</h1>

<p align="center">
  把本机 <strong>Codex</strong> 的内容安全、增量、可撤回地同步到 <strong>Antigravity</strong> 的迁移插件。<br/>
  <strong>支持对话文字 + 终端命令行双模式</strong> · 零第三方依赖（仅需 Python 3.9+）· macOS / Linux / Windows 通用
</p>

---

## 一、如何安装（任选一种）

### 方式 1：一行命令安装（推荐，全平台通用）

**macOS / Linux：**

```bash
git clone https://github.com/Rain1601/codex2agy.git ~/.gemini/config/plugins/codex2agy
```

（可选）如果你喜欢在终端直接敲 `codex2agy` 命令，多执行这一行：

```bash
mkdir -p ~/.local/bin && ln -sf ~/.gemini/config/plugins/codex2agy/bin/codex2agy ~/.local/bin/codex2agy
```

**Windows (PowerShell)：**

```powershell
git clone https://github.com/Rain1601/codex2agy.git "$env:USERPROFILE\.gemini\config\plugins\codex2agy"
```

### 方式 2：不敲命令（下载 ZIP）

1. 在本页面右上角点击 **Code → Download ZIP**。
2. 解压后把文件夹重命名为 `codex2agy`，放到：
   - macOS / Linux：`~/.gemini/config/plugins/codex2agy/`
   - Windows：`%USERPROFILE%\.gemini\config\plugins\codex2agy\`

### 方式 3：使用 Antigravity CLI

```bash
git clone https://github.com/Rain1601/codex2agy.git
agy plugin install ./codex2agy
```

> 安装完成后，Antigravity 会自动发现并启用 `codex2agy`（无需重启，可在 **Customizations / Plugins** 页面确认）。

### 更新与卸载

- **更新**：`cd ~/.gemini/config/plugins/codex2agy && git pull`
- **卸载**：在 Antigravity 插件页点击卸载，或直接删除 `~/.gemini/config/plugins/codex2agy` 目录

---

## 二、如何使用（文字 / 命令 任选）

### 模式 A：对话文字触发（在 Antigravity 聊天框里直接说）

打开任意新对话，直接发送：

- `用 codex2agy 扫描并迁移我的 Codex 内容`
- `帮我同步 Codex 的 Skills 和同项目会话`
- `撤回刚才的 codex2agy 迁移`

插件会先**只读扫描**并分 5 个模块展示清单，你勾选确认后才会写入。

### 模式 B：终端命令行触发（适合习惯用终端的同学）

如果你配置了软链，可直接敲 `codex2agy`；没配软链也可以直接跑 `~/.gemini/config/plugins/codex2agy/bin/codex2agy`（Windows 用 `python %USERPROFILE%\.gemini\config\plugins\codex2agy\skills\codex2agy\scripts\cli.py`）：

```bash
# 1. 只读扫描（预览全部 5 个模块）
codex2agy scan

# 2. 交互式勾选迁移（按提示回车/输入编号，Ctrl+C 中断默认自动撤销）
codex2agy migrate

# 演练模式（只看会创建什么，不写入任何文件）
codex2agy migrate --dry-run

# 3. 查看历史迁移批次
codex2agy history

# 4. 撤回最近一次迁移（或指定 --run-id <RUN_ID>）
codex2agy undo
codex2agy undo --run-id <RUN_ID> --dry-run
```

> 提示：在外部终端运行 `codex2agy migrate` 时，项目历史会话会自动导出为只读 Markdown 档案；如需直接同步到 Antigravity 侧边栏项目会话列表中，请在 Antigravity 对话或 Antigravity 内置终端中运行。

---

## 三、会迁移什么？（默认选项一览）

| 模块 | 迁移内容 | 默认选项 | 说明 |
| :--- | :--- | :---: | :--- |
| **1. 提示词** | `AGENTS.md`、`prompts/*.md` | **默认不迁** | 展示摘要，你主动勾选才迁 |
| **2. Skills** | Codex / `~/.agents` / 记忆沉淀的 Skills | **默认全选** | Antigravity 已有的同名技能自动跳过 |
| **3. 记忆** | `memory_summary.md` 与历史记忆目录 | **默认不迁** | 先展示两边机制差异，可选迁摘要或生成只读检索 Skill |
| **4. 第三方插件** | 兼容的插件 Skills 与 MCP | **默认全选** | 自动过滤 Codex 内部运行时插件（如 `node_repl`、`openai-*`） |
| **5. 同名 Project** | 同项目下的历史会话 | **未归档默认迁 / 已归档默认不迁** | 同目录自动强匹配，同名不同路径先由你确认；已归档迁入后仍保持已归档状态 |

---

## 四、安全与防误操作保证

- **只读源目录**：绝不修改、移动或删除 `~/.codex` 与 `~/.agents` 下任何文件。
- **重复不迁移、绝不覆盖**：已有同名 Skill / Rule / MCP / 会话一律自动跳过。
- **Session 强幂等**：内置纯 JSON 原子账本（`~/.gemini/antigravity/plugin_data/codex2agy/migration_ledger.json`）+ 跨平台互斥锁 + Antigravity 会话标签三重防重，**手抖重复触发或更换目标插件名也不会把同一个会话重复同步**。
- **中断默认自动撤销**：执行中途收到中断信号（`Ctrl+C`）或报错，默认自动回滚本次已写入的所有文件、MCP 条目和会话，不留半成品。
- **产物隔离可逆**：所有迁入的规则、技能、MCP 统一写入独立插件 `codex-imported`，随时可一键开关或卸载。
