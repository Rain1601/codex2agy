<p align="center">
  <img src="./assets/logo.png" alt="codex2agy logo" width="128" height="128" />
</p>

<h1 align="center">codex2agy</h1>

<p align="center">
  <a href="./README.md">English</a> · <strong>简体中文</strong>
</p>

<p align="center">
  把本机 <strong>Codex</strong> 的内容安全、增量、可撤回地同步到 <strong>Antigravity</strong> 的迁移插件。<br/>
  <strong>一句话丢给 Antigravity 自动安装迁移 · 也支持终端命令行</strong> · 零第三方依赖（Python 3.9+）· macOS / Linux / Windows 通用
</p>

---

## 一、最简单用法：直接丢给 Antigravity（推荐）

不用敲任何命令，直接把下面这句话发给 **Antigravity**：

```text
帮我安装 https://github.com/Rain1601/codex2agy 这个插件，安装完成后立刻帮我扫描并迁移本机 Codex 的内容。
```

> **给 Antigravity Agent 的自动执行说明（Agent 读到本页可直接按此执行）：**
> 1. **安装插件**：将 `https://github.com/Rain1601/codex2agy.git` 克隆到 `~/.gemini/config/plugins/codex2agy`（Windows 为 `%USERPROFILE%\.gemini\config\plugins\codex2agy`）；若目录已存在且为 Git 仓库，执行 `git pull` 更新。
> 2. **可选 CLI 软链（macOS/Linux）**：若 `~/.local/bin` 存在或可创建，执行 `chmod +x ~/.gemini/config/plugins/codex2agy/bin/codex2agy && ln -sf ~/.gemini/config/plugins/codex2agy/bin/codex2agy ~/.local/bin/codex2agy`。
> 3. **立即开始迁移（无需重启）**：读取 `~/.gemini/config/plugins/codex2agy/skills/codex2agy/SKILL.md`，严格按其中的 Step 1（只读扫描）→ Step 2（按用户语言分 5 模块展示清单并等待确认，**未确认前绝不写入**）→ Step 3（事务化增量迁移）执行。

---

## 二、手动安装（适合习惯自己用终端的同学）

### 方式 1：一行命令（macOS / Linux / Windows）

**macOS / Linux：**

```bash
git clone https://github.com/Rain1601/codex2agy.git ~/.gemini/config/plugins/codex2agy
```

（可选）注册全局 `codex2agy` 终端命令：

```bash
mkdir -p ~/.local/bin && ln -sf ~/.gemini/config/plugins/codex2agy/bin/codex2agy ~/.local/bin/codex2agy
```

**Windows (PowerShell)：**

```powershell
git clone https://github.com/Rain1601/codex2agy.git "$env:USERPROFILE\.gemini\config\plugins\codex2agy"
```

### 方式 2：下载 ZIP 解压

1. 点击本页右上角 **Code → Download ZIP**。
2. 解压并重命名为 `codex2agy`，放到：
   - macOS / Linux：`~/.gemini/config/plugins/codex2agy/`
   - Windows：`%USERPROFILE%\.gemini\config\plugins\codex2agy\`

### 方式 3：使用 Antigravity CLI

```bash
git clone https://github.com/Rain1601/codex2agy.git
agy plugin install ./codex2agy
```

- **更新**：`cd ~/.gemini/config/plugins/codex2agy && git pull`
- **卸载**：在 Antigravity 插件页卸载，或直接删除 `~/.gemini/config/plugins/codex2agy`

---

## 三、日常怎么用（文字 / 终端 任选）

### 模式 A：对话文字触发

在 Antigravity 任意新对话里直接说：

- `用 codex2agy 扫描并迁移我的 Codex 内容`
- `帮我同步 Codex 的 Skills 和项目会话`
- `撤回刚才的 codex2agy 迁移`

### 模式 B：终端命令行触发（支持 `--lang auto|zh|en`）

```bash
# 1. 只读扫描（预览全部 5 个模块）
codex2agy scan
codex2agy --lang zh scan

# 2. 交互式勾选迁移（回车采纳默认，Ctrl+C 中断默认自动撤销）
codex2agy migrate
codex2agy migrate --dry-run

# 3. 查看历史迁移批次
codex2agy history

# 4. 撤回最近一次或指定批次迁移
codex2agy undo
codex2agy undo --run-id <RUN_ID> --dry-run
```

> 提示：在外部终端运行 `codex2agy migrate` 时，项目历史会话会自动导出为只读 Markdown 档案并创建对应项目配置；如需同时写入 Antigravity 侧边栏会话列表，请在 Antigravity 对话或内置终端中运行。

---

## 四、会迁移什么？（默认选项一览）

| 模块 | 迁移内容 | 默认选项 | 说明 |
| :--- | :--- | :---: | :--- |
| **1. 提示词** | `AGENTS.md`、`prompts/*.md` | **默认不迁** | 展示摘要，你主动勾选才迁 |
| **2. Skills** | Codex / `~/.agents` / 记忆沉淀的 Skills | **默认全选** | Antigravity 已有的同名技能自动跳过 |
| **3. 记忆** | `memory_summary.md` 与历史记忆目录 | **默认不迁** | 先展示两边机制差异，可选迁摘要或生成只读检索 Skill |
| **4. 第三方插件** | 兼容的插件 Skills 与 MCP | **默认全选** | 自动过滤 Codex 内部运行时插件（如 `node_repl`、`openai-*`） |
| **5. 项目与会话** | 同路径已有项目 + Codex 独有项目（自动新建） | **未归档默认迁 / 已归档默认不迁** | 按本地真实路径自动判断：同路径直接合并会话，Codex 独有本地项目自动在 Antigravity 新建并同步；已归档迁入后仍保持已归档状态 |

---

## 五、安全与防误操作保证

- **只读源目录**：绝不修改、移动或删除 `~/.codex` 与 `~/.agents` 下任何文件。
- **重复不迁移、绝不覆盖**：已有同名 Skill / Rule / MCP / Project / 会话一律自动跳过。
- **Session 强幂等**：内置纯 JSON 原子账本（`~/.gemini/antigravity/plugin_data/codex2agy/migration_ledger.json`）+ 跨平台互斥锁 + Antigravity 会话标签三重防重，**手抖重复触发或更换目标插件名也不会把同一个会话重复同步**。
- **中断默认自动撤销**：执行中途收到中断信号（`Ctrl+C`）或报错，默认自动回滚本次已写入的所有文件、新建项目配置、MCP 条目和会话，不留半成品。
- **产物隔离可逆**：所有迁入的规则、技能、MCP 统一写入独立插件 `codex-imported`，随时可一键开关或卸载。
