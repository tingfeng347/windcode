<h1 align="center">Windcode</h1>

<p align="center">
  <img src="https://raw.githubusercontent.com/tingfeng347/windcode/master/assets/windcode-neon-wind-core.svg" width="260" alt="Windcode Logo">
</p>

<p align="center">
  <em>安全、可扩展的终端 Coding Agent，让 AI 在真实代码仓库中理解项目、修改代码、执行命令并完成验证。</em>
</p>

<p align="center">
  <a href="https://pypi.org/project/windcode/"><img src="https://img.shields.io/pypi/v/windcode?logo=pypi&label=PyPI" alt="PyPI version"></a>
  <a href="https://pypi.org/project/windcode/"><img src="https://img.shields.io/pypi/pyversions/windcode?logo=python" alt="Python versions"></a>
  <a href="https://github.com/tingfeng347/windcode/actions/workflows/ci.yml"><img src="https://github.com/tingfeng347/windcode/actions/workflows/ci.yml/badge.svg" alt="Cross-platform CI"></a>
  <a href="https://github.com/tingfeng347/windcode/stargazers"><img src="https://img.shields.io/github/stars/tingfeng347/windcode?logo=github&label=Stars" alt="GitHub stars"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-65a30d" alt="Apache-2.0 license"></a>
  <br>
  <img src="https://img.shields.io/badge/Textual-TUI-111827" alt="Textual TUI">
  <img src="https://img.shields.io/badge/MCP-enabled-0ea5e9" alt="MCP enabled">
  <img src="https://img.shields.io/badge/Multi--agent-ready-7c3aed" alt="Multi-agent ready">
  <img src="https://img.shields.io/badge/Python-SDK-3776AB?logo=python&logoColor=white" alt="Python SDK">
</p>

<p align="center">
  <b>中文</b> · <a href="README.en.md">English</a>
</p>

---

## 项目简介

Windcode 是一个面向真实代码仓库的终端 Coding Agent。它可以理解项目、修改文件、执行命令、
运行测试，并在高风险操作前请求授权；同时提供多模型接入、多智能体协作、MCP/Skills/Plugins
扩展、会话恢复和长期记忆。交互界面基于 Textual，核心运行时也可以作为 Python SDK 使用。

它解决的核心问题是：让模型在真实、可审计、可回滚的开发环境中完成编码任务，而不是只在玩具
示例里演示。因此 Windcode 内置了权限审批、进程沙箱、会话持久化、运行预算和 Trace 追踪，让
开发者能在同一个 TUI 里完成从对话、改代码到运行测试、审查变更的完整闭环。

- 面向人群：希望把 AI 接入日常开发流程的开发者与团队。
- 使用形态：交互式 TUI（基于 Textual），核心运行时也可作为 Python SDK 使用。
- 设计理念：安全优先、默认可审计、易于通过 MCP / Skills / Plugins 扩展。

## 演示

<p align="center">
  <img src="https://pic1.imgdb.cn/i/033rgL8ytDrAySvBniqhgs.png" alt="Windcode TUI" width="920">
</p>

![2026-07-19 00-21-22.png](https://pic1.imgdb.cn/i/033rhoraACOSdTMUADV8IH.png)

---

![2026-07-19 00-12-18.png](https://pic1.imgdb.cn/i/033rhryNejzD7nUXIJxqqT.png)

---

![2026-07-19 00-28-41.png](https://pic1.imgdb.cn/i/033ri07lrFD4Pt6SIu3Maz.png)

## 功能

### 代码工作台

- 在同一个 TUI 中完成对话、文件读取与搜索、补丁修改、Shell 命令、测试和构建。
- 工具调用、推理状态、耗时、Token 用量、审批请求和子智能体进度实时展示。
- 支持任务队列、运行中取消、模型流重试以及空闲超时，网络流中断不会无限卡住界面。
- 内置 Provider、扩展、长期记忆、会话和历史回退管理界面。
- 未配置模型、Provider 配置无效或凭据文件不可读时仍可进入 TUI；界面会显示原因，并引导重新连接
  Provider，不会因为模型初始化失败而退出整个应用。
- 提供异步 Python SDK，可订阅结构化事件、响应审批、取消运行、压缩上下文和管理子智能体。

### 多模型与可靠运行

- 原生支持 Anthropic Messages、OpenAI Responses 和 OpenAI-compatible 三种协议。
- 内置 OpenAI、DeepSeek、Moonshot AI、SiliconFlow、OpenRouter、智谱 AI、
  阿里云、Groq、Mistral、xAI 和 Google Gemini 配置预设，也可连接自定义兼容端点。
- 支持主 Provider、显式 fallback chain、流式文本/推理/工具调用、网络错误重试和模型回退。
- Provider 可直接在 `/model` 管理界面中新增、编辑、断开、设为默认和加载模型列表；API Key
  可保存到独立凭据存储，也可通过环境变量提供。
- 模型流连续无数据时自动超时并重试，`model_stream_idle_timeout_seconds` 可配置。
- 上下文达到阈值时自动压缩，也可以使用 `/compact` 主动请求压缩。

### 多智能体协作

- 支持 `explicit` 和 `proactive` 两种委派策略，以及 researcher、worker、verifier 三类角色。
- 可以并行派发独立任务，也可以通过 `collaborate_subagents` 进行 division、negotiation 或
  hybrid 协作，由参与者分轮交换结果，再由独立 verifier 汇总。
- 子智能体之间支持受控消息传递、同步轮次、超时、取消和聚合预算，TUI 会持续显示每个任务状态。
- 写任务使用独立 Git Worktree，完成后检查提交、变更文件和验证结果，再由父智能体决定是否集成。
- 子智能体继承经过角色和任务类型过滤的工具、MCP、Skills、权限与沙箱边界，禁止递归创建子智能体。

### MCP、Skills、Hooks 与插件

- MCP 同时支持 stdio 和 Streamable HTTP，可调用 Tools、Resources、Resource Templates 和 Prompts。
- `enable` 控制服务器是否可见，`required` 只控制已启用服务器是否在启动阶段主动连接；单个 MCP
  加载失败会显示降级状态，不会阻断普通对话；启动完成后会显示已加载、失败和按需加载数量。
- 少量 MCP 工具可直接注入，较大目录通过 `search_mcp_tools` 按需发现；模型调用名统一使用
  `mcp_` 前缀，同名工具会自动消歧。
- Skills 从项目 `.windcode/skills/<skill-name>/SKILL.md` 和用户
  `~/.windcode/skills/<skill-name>/SKILL.md` 发现，同名时项目级覆盖用户级，并支持 `$skill-name` 激活。
- 本地插件通过 `.windcode-plugin/plugin.toml` 组合 Skills、MCP Servers、Hooks 和自定义命令，
  支持安装、信任、启用、禁用、检查和显式 reload。
- Hooks 覆盖会话、运行、工具策略前后、权限申请、上下文压缩及子智能体生命周期；决策 Hook
  可以拒绝操作或收紧工具权限。

### 会话、记忆与可观测性

- 会话和事件增量持久化，支持恢复已有会话、选择历史输入回退、修改原输入后重新发送。
- 长期记忆区分用户画像、项目事实、经验、SOP 和参考资料，支持候选确认、拒绝、遗忘、搜索、
  激活策略和索引重建。
- 稳定用户事实可以自动激活；经验和 SOP 结合真实变更与验证结果生成，避免把未验证结论直接固化。
- Trace 记录模型、工具、审批、扩展和子智能体事件，并提供保留天数、容量和瞬态事件配置。
- 大型工具结果可外置为会话 Artifact，减少上下文膨胀，同时保留可追溯引用。

### 权限、沙箱与跨平台

- 提供 `plan`、`default`、`accept_edits` 和 `full_access` 四种权限模式，可在运行中切换。
- 根据工具副作用、命令解析、工作目录、网络需求和沙箱状态计算风险，并支持仅本次允许、拒绝、
  取消命令以及项目级命令前缀规则。
- Linux 使用 Bubblewrap，macOS 使用 Seatbelt；支持 `read_only`、`workspace_write` 和
  `danger_full_access` 三种沙箱 preset。
- Windows 默认使用 PowerShell，不启用 OS 沙箱；配置的沙箱 preset 在 Windows 上确定性降级为
  `danger_full_access`，避免不可用后端反复触发提示。权限模式与危险命令检查仍然生效。

## 快速开始

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)，支持 Linux、macOS 和 Windows。

```bash
uv tool install windcode
windcode .
```

首次启动无需提前配置模型。在 TUI 中执行 `/model`，选择 Provider、填写模型和 API Key 即可；
密钥会进入独立凭据存储，不会写入项目配置。

其他安装方式：

```bash
# npm 包装器，需要 Node.js 20+ 和 uv
pnpm add --global windcode

# 安装到当前 Python 环境
uv pip install windcode
```

### 使用入口

| 入口 | 命令 | 说明 |
| --- | --- | --- |
| TUI | `windcode /path/to/project` | 默认终端工作台 |
| Web | `windcode web /path/to/project` | 打开 `http://127.0.0.1:8765` |
| Desktop | `windcode desktop /path/to/project` | 使用系统 WebView 打开桌面窗口 |
| Docker | `docker run --rm -it -v "$PWD:/workspace" ghcr.io/tingfeng347/windcode:latest` | 挂载当前目录并启动 TUI |

Web 仅监听本机回环地址，支持 `--port` 和 `--no-open`。Desktop 在 Linux 上优先使用
WebKitGTK；Windows/macOS 或 Linux fallback 安装时使用 `uv tool install "windcode[desktop]"`。
完整容器说明见 [GHCR 镜像说明](docs/ghcr.md)。

常用启动参数：

```text
--config FILE
--model PROVIDER_OR_MODEL
--resume SESSION_ID
--permission-mode plan|default|accept_edits|full_access
--sandbox / --no-sandbox
```

## 常用命令与快捷键

```text
/new                         新建会话
/resume [SESSION_ID]         恢复会话
/rewind                      选择历史输入并回退
/model [PROVIDER_ALIAS]      管理或切换模型与 Provider
/memory [ACTION]             管理长期记忆
/extensions [ACTION] [ID]    管理扩展、插件与信任状态
/compact                     压缩当前上下文
/clear                       清空当前消息显示
/agents                      查看子智能体
/status                      查看运行状态
/help                        查看全部命令及插件命令
/quit                        退出 Windcode

Shift+Tab                    循环切换权限模式
Esc（连续两次）              中断当前运行
```

## 配置

推荐直接在 `/model` 和管理界面中完成配置。需要文件配置时，以
[`.windcode/config.toml.example`](.windcode/config.toml.example) 为参考：用户级配置位于
`~/.windcode/config.toml`，项目级 `.windcode/config.toml` 优先级更高。

- API Key 只应通过环境变量或凭据存储提供，不要写入 TOML 或提交到 Git。
- 项目状态默认位于 `.windcode/`，包含会话、记忆、Trace、扩展和 Worktree；这些运行数据不要提交。
- MCP 支持 `stdio` 和 `streamable_http`；`enable` 控制可见性，`required` 只控制已启用服务的
  启动连接。
- 子智能体支持 `explicit` 和 `proactive` 模式，并受并发数、单任务预算和聚合预算共同限制。
- 运行预算可限制模型步骤、工具调用、总耗时、模型流空闲时间和 Shell 命令时间。
- 沙箱 preset 包括 `read_only`、`workspace_write` 和 `danger_full_access`。Linux 使用 Bubblewrap，
  macOS 使用 Seatbelt；Windows 不启用 OS 沙箱，但权限审批和危险命令检查仍然生效。

Provider 缺失或配置无效时，TUI 仍会启动并给出修复入口；TOML 语法错误等基础配置错误需要先按
终端提示修复。

## 从源码开发

```bash
uv sync --frozen --all-groups
uv run windcode .
```

Web 前端位于 `web/`，由 React、TypeScript 和 Vite 构建：

```bash
pnpm install
pnpm web:dev    # API :8765 + Vite :5173
pnpm web:build  # 构建到 src/windcode/web/static/
pnpm web:test
```

## 许可证

[Apache-2.0](LICENSE)
