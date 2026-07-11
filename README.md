# Windcode  Agent

基于 **Python 3.12 + Textual + 异步事件内核** 的本地编码 Agent。项目以统一的消息、事件、工具和权限协议连接终端工作台与 Python SDK，可完成代码读取、文件修改、命令执行、测试验证和结果汇总，并提供多模型协议、显式故障回退、会话恢复、上下文压缩与 Linux 沙箱能力。

## 项目简介

Windcode 面向需要在本地工作区中安全执行编码任务的开发者。它采用分层事件内核，终端界面和嵌入式 SDK 共享同一套 Agent 行为，所有模型调用、工具执行、审批、用量和终态均可观察、可持久化、可审计。

当前版本是经过完整自动化验收的本地编码 Agent，重点保证编码闭环、用户控制和故障恢复，并支持任务级临时子智能体与独立 Worktree 写入隔离。项目暂不包含长期团队、MCP、Skills、插件生命周期、跨会话长期记忆、浏览器控制、Web 界面、IDE 集成和远程执行后端。

### 核心功能

- **完整编码闭环**：支持读取文件、修改代码、运行命令、执行测试并基于实际结果生成报告
- **统一事件流**：流式展示文本、推理状态、工具调用、审批、用量、错误和最终结果
- **三类模型协议**：支持 Anthropic Messages、OpenAI Responses 和 OpenAI-compatible Chat Completions
- **跨会话凭据**：API Key 以 OpenCode 兼容结构保存在权限受限的用户凭据文件中
- **显式模型回退**：瞬时错误最多重试两次，失败后仅按用户配置的回退链切换模型
- **八种内置工具**：提供文件读取、写入、精确编辑、补丁、文件匹配、文本搜索、Shell 和用户提问
- **安全并发调度**：只读工具可并行执行，写入、Shell 和交互工具保持顺序且结果稳定
- **权限与审批**：提供计划、默认、自动编辑和完全授权四种模式，高风险操作由用户确认
- **Linux 沙箱**：通过 bubblewrap 隔离 Shell；不可用时明确显示降级状态并收紧审批
- **会话恢复与分支**：持久化对话和工具状态，支持重启恢复、历史回退和非破坏性分支
- **长上下文管理**：自动裁剪低价值内容并生成结构化检查点，保留目标、决策、进度和验证证据
- **临时子智能体**：主智能体可并行委派有界任务，写入任务使用独立 Worktree 并由父智能体审查集成
- **可嵌入 SDK**：外部程序可提交任务、消费事件、响应审批、取消运行并注册自定义工具或模型传输
- **本地可观测性**：保存脱敏 JSONL 轨迹和大型结果 artifact，默认不发送遥测

## 技术架构

```text
┌──────────────────────────────────────────────────────────────┐
│                         交互入口层                            │
│       CLI / Textual TUI / Python SDK / 自定义集成            │
└──────────────────────────────┬───────────────────────────────┘
                               │ 类型化异步事件流
┌──────────────────────────────┴───────────────────────────────┐
│                         Agent Runtime                        │
│  Agent Loop / 预算与取消 / 重试与回退 / 工具调度 / 完成报告  │
└───────────────┬──────────────────────┬───────────────────────┘
                │                      │
┌───────────────┴──────────────┐ ┌─────┴───────────────────────┐
│        模型与上下文层         │ │       工具与安全执行层       │
│ Providers / Context Engine   │ │ Tools / Policy / Sandbox    │
│ 指令加载 / 检查点压缩         │ │ 路径校验 / 审批 / bubblewrap │
└───────────────┬──────────────┘ └─────┬───────────────────────┘
                │                      │
┌───────────────┴──────────────────────┴───────────────────────┐
│                         本地持久化层                          │
│       Session Event Tree / Artifacts / Redacted Traces      │
└──────────────────────────────────────────────────────────────┘
```

## 技术栈

| 技术 | 说明 |
| --- | --- |
| Python 3.12 | 项目运行时与类型系统基础 |
| asyncio | 模型流、工具调度、审批等待和取消传播 |
| Textual | 终端聊天工作台和交互组件 |
| Pydantic | 配置、工具参数和边界数据校验 |
| Anthropic SDK | Anthropic Messages 异步传输 |
| OpenAI SDK | OpenAI Responses 异步传输 |
| aiohttp | OpenAI-compatible 协议和异步 HTTP 基础 |
| bubblewrap | Linux Shell 进程隔离与默认断网 |
| platformdirs | XDG 用户配置和本地状态目录解析 |
| JSONL | 会话事件树和本地运行轨迹持久化 |
| uv + Hatchling | 依赖锁定、环境管理和包构建 |
| Pytest | 单元、协议契约、集成、TUI 和端到端测试 |
| Ruff + Pyright | 格式、静态检查和严格类型检查 |

## 项目结构

```text
windcode/
├── .windcode/
│   └── config.toml.example       # 完整项目配置示例
├── spec/
│   ├── spec.md                   # 行为需求与验收标准
│   ├── plan.md                   # 技术设计与模块交互
│   ├── task.md                   # 实现任务拆解
│   └── checklist.md              # 已执行的验收清单
├── src/windcode/
│   ├── cli.py                    # 命令行参数与 TUI 启动入口
│   ├── sdk.py                    # Windcode 和 RunHandle 公共 SDK
│   ├── types.py                  # 稳定公共类型导出
│   ├── domain/                   # 消息、事件、模型、错误和工具契约
│   ├── config/                   # 严格 TOML 模型与分层配置加载
│   ├── auth/                     # OpenCode 风格的受保护凭据文件
│   ├── instructions/             # 项目持久指令发现与优先级处理
│   ├── providers/                # 三类模型协议适配与错误归一化
│   ├── runtime/                  # Agent Loop、预算、调度、回退和报告
│   ├── tools/                    # 八种内置工具和工具注册表
│   ├── policy/                   # 权限模式、风险判断和审批策略
│   ├── sandbox/                  # bubblewrap 检测与命令封装
│   ├── sessions/                 # 会话事件树、分支和 artifacts
│   ├── context/                  # token 估算、裁剪和检查点压缩
│   ├── observability/            # 脱敏与本地 JSONL 轨迹
│   └── tui/                      # Textual 应用、命令与组件
├── tests/
│   ├── unit/                     # 纯模块与工具单元测试
│   ├── contract/                 # 三类模型协议共享契约测试
│   ├── integration/              # SDK、运行时、安全、会话和 TUI 测试
│   ├── e2e/                      # 完整模拟编码任务
│   └── smoke/                    # 可选真实 Provider 冒烟测试
├── LICENSE                       # Apache-2.0 许可证
├── pyproject.toml                # 项目元数据、依赖与质量工具配置
├── uv.lock                       # 可复现依赖锁文件
└── README.md
```

## 快速开始

### 环境要求

- Linux
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- bubblewrap，可选；缺失时 Windcode 会进入可见的安全降级模式
- 至少一个受支持模型服务的 API Key

### 1. 安装依赖

在项目根目录执行：

```bash
uv sync --frozen --all-groups
```

确认命令入口可用：

```bash
uv run windcode --help
uv run python -m windcode --help
```

### 2. 配置模型

复制项目配置模板：

```bash
cp .windcode/config.toml.example .windcode/config.toml
```

最小双模型配置示例：

```toml
primary_provider = "primary"
fallback_chain = ["backup"]

[providers.primary]
protocol = "openai_responses"
model = "gpt-5.2-codex"
api_key_env = "OPENAI_API_KEY"

[providers.backup]
protocol = "anthropic_messages"
model = "claude-sonnet-4-5"
api_key_env = "ANTHROPIC_API_KEY"

[permission]
mode = "default"

[sandbox]
enabled = true
network_enabled = false
```

可以继续通过环境变量提供密钥，适合 CI 和临时覆盖：

```bash
export OPENAI_API_KEY="你的_OpenAI_API_Key"
export ANTHROPIC_API_KEY="你的_Anthropic_API_Key"
```

支持的 Provider 协议如下：

| `protocol` | Provider 字段 | 说明 |
| --- | --- | --- |
| `anthropic_messages` | `model`、`api_key_env` 或 `credential_id` | Anthropic Messages |
| `openai_responses` | `model`、`api_key_env` 或 `credential_id` | OpenAI Responses |
| `openai_compatible` | 上述字段及 `base_url` | OpenAI-compatible Chat Completions |

启动 TUI 后，空闲时执行 `/model` 会打开 OpenCode 风格的模型选择器。左右方向键切换厂商分组，上下方向键移动模型，按 Enter 使用高亮模型；直接输入模型名、Provider 别名、协议或厂商名即可搜索。“管理 Provider”用于新增、编辑、设为默认和断开连接。模型元数据会原子写入工作区的 `.windcode/config.toml`；使用 `--config` 启动时写入对应的显式配置文件。

新增 Provider 时可以直接选择内置模板。当前包含 OpenAI、Anthropic、DeepSeek、Moonshot AI、SiliconFlow、OpenRouter、Zhipu AI、Alibaba Cloud、Groq、Mistral、xAI 和 Google Gemini，模板会自动填写协议、API Key 环境变量和 Base URL；选择“自定义 Provider”仍可手工配置任意 OpenAI-compatible 服务。

在 Provider 管理器中输入的 API Key 会跨会话保存。与 OpenCode 一致，Windcode 将凭据写入用户数据目录的 `windcode/auth.json`；Linux 默认路径为 `~/.local/share/windcode/auth.json`。文件使用 `{provider: {type: "api", key: "..."}}` 结构，目录权限为 `0700`、文件权限为 `0600`。项目 TOML 只保存 `credential_id` 和可选的 `api_key_env`，不会包含真实密钥。环境变量始终优先于已保存凭据，因此无需修改本地密钥即可在 CI 中覆盖。

也可以使用配置别名快速切换，不接受未配置的别名：

```text
/model codex
```

新增 Provider 时填写别名、协议和模型名称，并输入 API Key 或配置环境变量；`openai_compatible` 还必须填写 Base URL。编辑时 API Key 留空会保留原凭据。断开连接需要二次确认，并删除该 Provider 的已保存密钥；设为默认会同时更新 `primary_provider`，原默认模型会保留为故障回退目标。

`openai_compatible` 的 `base_url` 通常以 `/v1` 结尾。

### 3. 启动终端工作台

```bash
uv run windcode /path/to/project
```

常用启动参数：

```text
--config FILE
--model PROVIDER_OR_MODEL
--resume SESSION_ID
--permission-mode plan|default|accept_edits|full_access
--sandbox / --no-sandbox
```

在输入框提交编码任务。运行期间按 `Ctrl+C` 会取消模型请求或工具进程，空闲时按 `Ctrl+C` 退出程序。

### 4. 使用斜杠命令

```text
/new
/resume [SESSION_ID_OR_PREFIX]
/history
/rewind RECORD_ID_OR_PREFIX
/mode MODE
/model MODEL
/compact
/clear
/status
/agents
/help
/quit
```

运行过程中不允许切换模式、模型、恢复点或回退节点，避免改变在途任务的执行语义。

## 核心模块

### 1. Agent 循环

核心循环位于 `src/windcode/runtime/loop.py`：

```text
构造上下文
    ↓
流式调用模型
    ↓
解析并调度工具调用
    ↓
执行权限判断与用户审批
    ↓
追加工具结果并继续模型循环
    ↓
完成 / 取消 / 预算耗尽 / 不可恢复错误
```

默认预算为 40 个模型步骤、100 次工具调用和 30 分钟运行时间，Shell 默认超时为 120 秒。普通工具参数错误和执行错误会作为结构化结果反馈给模型，不会直接破坏 Agent Loop。

### 2. 模型传输与故障回退

`src/windcode/providers/` 将三类协议转换为统一的内部事件。Windcode 关闭 Provider SDK 自带重试，对限流、网络失败和服务端错误最多执行两次有限退避；持续失败后仅按 `fallback_chain` 切换，并在 TUI 和 SDK 事件流中明确展示原模型、原因和目标模型。

跨 Provider 回退时保留文本、工具调用和工具结果，移除不能安全迁移的私有推理状态。

### 3. 工具与权限策略

| 工具 | 用途 |
| --- | --- |
| `read_file` | 读取工作区 UTF-8 文件 |
| `write_file` | 创建或原子替换文件 |
| `edit_file` | 执行精确文本替换 |
| `apply_patch` | 预检并应用单文件或多文件补丁 |
| `glob` | 按模式匹配文件 |
| `grep` | 搜索工作区文本 |
| `shell` | 执行有界输出、支持超时和取消的 Shell 命令 |
| `ask_user` | 在运行期间向用户提出结构化问题 |

权限模式：

| 模式 | 行为 |
| --- | --- |
| `plan` | 允许读取、搜索和提问；拒绝写入和有副作用命令 |
| `default` | 自动允许读取；写入和 Shell 需要审批 |
| `accept_edits` | 自动允许工作区编辑；进程、网络和越界操作需要审批 |
| `full_access` | 跳过常规审批，但所有行为仍会记录和审计 |

文件工具会解析真实路径和符号链接，拒绝工作区逃逸，并使用内容摘要避免覆盖并发修改。多文件补丁会在任何写入前完成全量预检。

### 4. 沙箱执行

Linux 下默认使用 bubblewrap：系统目录只读、工作区可写、`/tmp` 独立且网络关闭。bubblewrap 不可用时，Windcode 会显示降级状态并把 Shell 提升为审批操作，不会静默声称已经隔离。

`--no-sandbox` 和 `full_access` 只能由用户在启动参数或配置中显式选择，模型无法自行扩大权限。

### 5. 会话与上下文恢复

会话和脱敏轨迹保存在平台 XDG state 目录，Linux 通常为：

```text
~/.local/state/windcode
```

使用 `--resume SESSION_ID` 或 `/resume SESSION_ID` 可恢复现有会话。TUI 会立即回放当前分支的用户与助手消息，模型上下文也从当前事件树头节点恢复。`/resume` 不带参数时显示包含摘要、最后更新时间、状态和短 ID 的会话选择器，并按最后更新时间从新到旧排列；命令参数支持唯一短 ID。

`/history` 显示当前分支的对话记录和记录短 ID，`/rewind RECORD_ID_OR_PREFIX` 可据此回退。空闲时执行 `/compact` 会安排在下一轮模型请求前压缩上下文；`/clear` 只清空当前界面，不删除会话历史。

`/rewind RECORD_ID` 会从指定历史节点创建新分支：旧分支后续消息不会进入新的模型上下文，但原始历史仍完整保留。大型工具结果保存在内容寻址 artifact 中；损坏的最后一行 JSONL 会被忽略；只有开始记录但没有持久结果的副作用操作会恢复为 `interrupted`，不会自动重放。

当前的持久化属于会话记忆，而不是跨会话长期记忆。上下文检查点只用于延续当前会话，不会自动提炼并共享到其他会话。Windcode 目前没有全局或项目级记忆库，也不会自动建立用户画像、归纳可复用经验、自我修改规则或将历史会话内容注入新会话。需要长期保留的项目知识应由用户明确写入 `AGENTS.md`、`WINDCODE.md`、`CLAUDE.md` 或 `HERMES.md` 等项目指令文件。

### 6. 项目指令

Windcode 从工作区根目录向当前目录逐层加载持久指令。每一层按以下顺序选择第一个存在的文件：

```text
AGENTS.md → WINDCODE.md → CLAUDE.md → HERMES.md
```

越接近当前工作目录的规则越晚注入，因此具有更高优先级。

### 7. 上下文压缩

上下文接近预算时，系统会先移除历史媒体并截断低价值的大型工具结果，再生成包含以下内容的结构化检查点：

- 任务目标
- 关键决策
- 相关文件
- 当前进度
- 未完成事项
- 验证证据
- 约束与下一步

压缩仅改变发送给模型的视图，不会删除原始会话审计记录。摘要失败时继续使用原上下文并报告错误。

## Python SDK 使用

SDK 不会隐式加载 TOML 配置，需要先加载工作区配置并传给 `Windcode.open()`：

```python
import asyncio
from pathlib import Path

from windcode import Windcode
from windcode.config import load_config
from windcode.types import ApprovalRequested, ApprovalResponse, RunRequest


async def run_task() -> None:
    workspace = Path.cwd()
    config = load_config(workspace)

    async with Windcode.open(config) as client:
        handle = client.start_run(
            RunRequest(
                prompt="修复失败的测试",
                workspace=workspace,
                session_id=None,  # 传入已有会话 ID 可恢复当前分支。
            )
        )

        async for event in handle:
            if isinstance(event, ApprovalRequested):
                await handle.respond(
                    ApprovalResponse(event.request_id, "allow_once")
                )

        result = await handle.result()
        print(result.status)
        print(result.changed_files)
        print(result.verification)


asyncio.run(run_task())
```

`RunHandle` 是异步事件迭代器，并提供以下方法：

- `respond()`：回复审批或结构化问题
- `cancel()`：取消模型请求、工具任务和子进程
- `compact()`：请求在下一个模型边界压缩上下文
- `result()`：幂等获取最终结果

`Windcode.list_sessions()` 可列出可恢复会话，`Windcode.rewind_session(session_id, record_id)` 可创建不覆盖原历史的新分支。

自定义工具实现公共 `Tool` 协议和 Pydantic 输入模型，通过 `client.register_tool(tool)` 注册。自定义模型传输实现 `stream(ModelRequest)` 与 `aclose()`，通过 `client.register_transport(alias, model, transport, primary=True)` 注册。两种扩展都会复用内置参数校验、权限、事件、取消、会话和轨迹机制。

## 配置优先级

配置按以下顺序叠加，后者覆盖前者：

```text
内建默认值
    ↓
~/.config/windcode/config.toml
    ↓
<workspace>/.windcode/config.toml
    ↓
--config 指定文件
    ↓
CLI / SDK 显式覆盖
```

未知字段会产生配置错误。API Key 的读取顺序是 `api_key_env` 指向的环境变量、用户级 `auth.json`。不要把真实密钥直接写入 TOML。

## 常见问题

### 启动后提示 `no model transport is configured` 怎么办？

确认 `.windcode/config.toml` 中存在 `providers` 和 `primary_provider`，然后使用 `/model` 打开 Provider 管理器检查连接状态。也可以确认 `api_key_env` 指向的环境变量已经设置：

```bash
uv run windcode --help
```

再检查配置文件路径是否位于实际工作区内，或通过 `--config` 显式指定。

### bubblewrap 不可用怎么办？

Windcode 仍可运行，但会显示沙箱降级状态，并要求用户审批 Shell 操作。安装系统发行版提供的 `bubblewrap` 后重新启动即可启用正式隔离路径。

### 如何继续上一次会话？

先在 TUI 会话列表或 `Windcode.list_sessions()` 中找到会话 ID，再执行：

```bash
uv run windcode /path/to/project --resume SESSION_ID
```

也可以在空闲状态使用 `/resume SESSION_ID`。恢复会沿当前分支继续，不会自动重复未确认的副作用操作。

### 如何从历史节点重新开始？

选择目标记录 ID 后执行 `/rewind RECORD_ID`。Windcode 会创建新分支，不会删除或覆盖原分支历史。

### 如何切换模型？

启动时使用 `--model MODEL_ALIAS`，或在空闲状态使用 `/model` 打开管理器、使用 `/model MODEL_ALIAS` 快速切换。自动故障切换只会使用配置中的 `fallback_chain`，不会静默选择未配置模型。

### 为什么真实 Provider 测试被跳过？

真实模型冒烟测试默认禁用，避免离线测试依赖付费密钥。需要显式设置 `WINDCODE_REAL_SMOKE=1`，并提供对应 Provider 的凭据和模型环境变量。

## 开发与验收

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
uv build
```

当前 MVP 验收基线：

```text
Ruff format：126 个文件格式正确
Ruff lint：通过
Pyright strict：0 errors
Pytest：161 passed，3 skipped
构建：wheel 和源码分发包均成功
验收清单：53/53
```

三个默认 skip 均为真实 Provider 冒烟测试。普通测试套件完全离线，不依赖模型密钥。wheel 和源码分发包均包含 Apache-2.0 `LICENSE`。

## 参考资源

- [Textual 文档](https://textual.textualize.io/)
- [Pydantic 文档](https://docs.pydantic.dev/)
- [Anthropic API 文档](https://docs.anthropic.com/)
- [OpenAI API 文档](https://platform.openai.com/docs/)
- [aiohttp 文档](https://docs.aiohttp.org/)
- [uv 文档](https://docs.astral.sh/uv/)

Windcode 使用 Apache-2.0 许可证发布。
