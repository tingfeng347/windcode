# Windcode

Windcode 是一个面向真实代码仓库的终端 Coding Agent。它可以理解项目、修改文件、执行命令、
运行测试，并在高风险操作前请求授权；同时提供多模型接入、多智能体协作、MCP/Skills/Plugins
扩展、会话恢复和长期记忆。交互界面基于 Textual，核心运行时也可以作为 Python SDK 使用。

## 演示
![2026-07-18 23-16-58.png](https://pic1.imgdb.cn/i/033rgL8ytDrAySvBniqhgs.png)

---

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
- 内置 OpenAI、Anthropic、DeepSeek、Moonshot AI、SiliconFlow、OpenRouter、智谱 AI、
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
- Windows 默认使用 PowerShell。当前不提供系统级文件沙箱，因此会如实显示降级状态，并继续由
  权限策略保护高风险命令，而不会伪装成已隔离。

## 快速开始

环境要求：Linux、macOS 或 Windows，Python 3.11+、[uv](https://docs.astral.sh/uv/)。

Linux 使用 Bubblewrap，macOS 使用 Seatbelt。Windows 暂不提供系统级进程沙箱，PowerShell
命令通过权限策略逐次授权；`full_access` 模式下可按配置直接执行。

沙箱 preset 为 `read_only`、默认的 `workspace_write` 和显式的
`danger_full_access`。旧配置 `enabled=true/false` 仍可读取，并分别映射到
`workspace_write/danger_full_access`。命令联网和沙箱外运行单独审批；项目级命令前缀规则保存
在 state root 的 `permissions/projects/`，不会写入仓库。

从 PyPI 安装命令行工具：

```bash
uv tool install windcode
windcode /path/to/project
```

也可以安装到当前 Python 环境：

```bash
uv pip install windcode
```

从源码运行：

```bash
uv sync --frozen --all-groups
uv run windcode /path/to/project
```

首次运行不要求预先配置模型。进入 TUI 后输入 `/model` 即可连接 Provider。若希望使用文件配置，
可将 `.windcode/config.toml.example` 复制到项目目录，再修改其中的模型和扩展设置。

最小模型配置：

```toml
primary_provider = "primary"

[providers.primary]
protocol = "openai_compatible"
model = "your-model"
base_url = "https://example.com/v1"
api_key_env = "MODEL_API_KEY"
```

密钥应通过环境变量或 Windcode 凭据存储提供，不要写入项目配置。

```bash
export MODEL_API_KEY="..."
uv run windcode .
```

如果没有配置 Provider、API Key 缺失、Provider 配置字段无效，或凭据文件损坏，Windcode 会保留
TUI 和扩展功能并显示具体原因。此时输入任务或执行 `/model` 会打开 Provider 管理器。只有 TOML
语法错误或与 Provider 无关的基础配置错误仍会阻止启动，因为这类配置无法安全恢复。

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

## MCP Server

```toml
[extensions]
enabled = true

[extensions.mcp_servers.example]
transport = "streamable_http"
url = "https://example.com/mcp"
enable = true
required = false
```

stdio MCP 示例：

```toml
[extensions.mcp_servers.local-example]
transport = "stdio"
command = "uvx"
args = ["example-mcp-server"]
enable = true
required = false
```

`enable = false` 的服务器不会连接、不会参与工具搜索，也不会注入模型上下文。`required` 只在
服务器启用时表示启动阶段主动连接；连接失败会显示降级状态，但不会阻断普通消息。首次生成的
用户配置和 `.windcode/config.toml.example` 当前会启用 `gaodemap-mcp`；不需要时请将其
`enable` 改为 `false`。

## 多智能体配置

```toml
[subagents]
mode = "explicit" # explicit | proactive
max_tasks = 8
max_concurrent = 4
max_model_steps = 20
max_tool_calls = 50
max_runtime_seconds = 900
max_total_model_steps = 80
max_total_tool_calls = 200
```

`explicit` 只在用户明确要求委派、并行或使用子智能体时开放委派；`proactive` 允许模型根据任务
复杂度主动拆分。并发数、单任务预算和聚合预算会同时生效。

## 运行预算与流超时

```toml
[budgets]
max_model_steps = 40
max_tool_calls = 100
max_runtime_seconds = 1800
model_stream_idle_timeout_seconds = 60
shell_timeout_seconds = 120
```

模型流在配置时间内没有产生任何事件时会按网络错误进入重试/回退流程；手动中断则记录为正常
取消，不会被包装成 Provider 失败。

## 本地状态

Windcode 将记忆、会话、trace、扩展状态和 Worktree 统一存放在选定的状态根下：

```toml
[storage]
project_state_root = ".windcode"
user_storage_root = "~/.windcode"
```

用户级配置固定读取 `~/.windcode/config.toml`；项目中的 `.windcode/config.toml` 优先级更高。
配置项目状态根时优先使用项目目录；未配置时使用 `~/.windcode`。Skill 会同时扫描两边的
`skills/`，同名时项目级覆盖用户级。项目 `.windcode/config.toml` 和 `.windcode/` 下的运行
状态都不应提交到 Git。

模型 API Key 不写入 TOML，而是保存在用户存储根下的 `auth.json`。Windcode 不会在错误信息或
项目配置中回显密钥内容。

## 常见问题

### 启动后提示尚未配置模型 Provider

这是可恢复状态，不会影响查看扩展、MCP、Skills、会话和长期记忆。执行 `/model`，选择内置预设
或自定义兼容端点，填写模型 ID 和 API Key 后保存即可。

### Provider 配置或凭据错误

Windcode 会临时停用不可用的模型连接并继续启动。欢迎页会显示配置校验或凭据读取错误；通过
`/model` 修复 Provider 字段或重新连接后，新配置会立即生效，无需重启应用。若 `auth.json`
已经损坏或不可读，需要先备份并修复该文件；若配置文件本身不是合法 TOML，请先根据终端错误
修复对应文件的语法。

## License

[Apache-2.0](LICENSE)
