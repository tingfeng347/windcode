# Windcode 纵向 MVP Plan

## 架构概览

采用单向依赖的分层事件内核：

```text
CLI → Textual TUI → Public SDK → Agent Runtime
                                  ├─ Model Transports
                                  ├─ Tool Runtime
                                  ├─ Policy / Sandbox
                                  ├─ Context Engine
                                  └─ Session / Trace Store
```

- `domain`：不可变消息、事件、工具调用、用量、错误与权限类型，不依赖具体 SDK 或 UI。
- `providers`：将三种模型协议转换为统一模型事件流。
- `runtime`：Agent Loop、预算、重试、模型回退、工具调度和取消传播。
- `infrastructure`：会话、配置、指令加载、沙箱和本地轨迹。
- `sdk`：唯一稳定公共入口，负责装配运行时并暴露异步事件流。
- `tui`：只消费 SDK，不直接访问模型、工具或会话内部实现。

## 核心数据结构

### 统一消息模型

`Message` 包含角色、内容块、时间和提供商元数据。内容块包括文本、可展示推理摘要、工具调用、工具结果和附件引用。提供商要求回传的签名或加密状态作为不可展示的 opaque metadata 保存；跨提供商回退时保留文本、工具调用和结果，丢弃不能迁移的私有推理块。

### 统一事件模型

所有事件包含 `event_id`、`session_id`、`run_id`、时间和轮次。事件联合包含 `RunStarted`、`ModelStarted`、`TextDelta`、`ReasoningStatus`、`ToolStarted`、`ToolProgress`、`ToolFinished`、`ApprovalRequested`、`UserInputRequested`、`UsageUpdated`、`ModelRetrying`、`ModelFallback`、`ContextCompacted`、`RunCompleted`、`RunFailed` 和 `RunCancelled`。

事件先持久化，再分发给 SDK/TUI；消费者可从事件序号恢复。

### Python SDK

```python
async with Windcode.open(config=...) as client:
    run = client.start_run(RunRequest(prompt="修复测试", workspace=Path.cwd()))
    async for event in run:
        if isinstance(event, ApprovalRequested):
            await run.respond(ApprovalResponse(...))
    result = await run.result()
```

公共接口为 `Windcode.open()`、`start_run()`、`RunHandle.respond()`、`RunHandle.cancel()`、`RunHandle.result()`、`register_tool()` 和 `register_transport()`。仅 `windcode.sdk` 与 `windcode.types` 承诺公共兼容性。

### 工具接口

```python
class Tool(Protocol):
    name: str
    input_model: type[BaseModel]
    effects: frozenset[ToolEffect]

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult: ...
```

首期内置 `read_file`、`write_file`、`edit_file`、`apply_patch`、`glob`、`grep`、`shell` 和 `ask_user`。只读调用可并行；写入、Shell 和交互调用独占调度。

### 模型传输接口

```python
class ModelTransport(Protocol):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...
```

Anthropic Messages 和 OpenAI Responses 使用官方异步 SDK 的 aiohttp 后端；OpenAI-compatible Chat Completions 直接使用 aiohttp。关闭 SDK 内建重试，由 Windcode 统一处理认证、限流、网络、服务端、上下文、请求、内容限制和取消错误。

## 模块设计

### Agent Runtime

- 执行“构造上下文 → 模型流 → 工具调用 → 权限判定 → 工具执行 → 追加结果”循环。
- 默认限制为 40 次模型步骤、100 次工具调用和 30 分钟运行时间。
- Shell 默认超时 120 秒。
- 使用 `asyncio.TaskGroup` 管理只读工具并发和取消传播。

### Policy 与 Sandbox

| 模式 | 行为 |
|---|---|
| `plan` | 允许读取、搜索和询问；拒绝写入及有副作用 Shell |
| `default` | 读取自动允许；写入和 Shell 请求审批 |
| `accept_edits` | 工作区写入自动允许；高风险、网络及越界操作请求审批 |
| `full_access` | 跳过常规审批但记录行为；沙箱独立控制 |

- 文件路径规范化并检查符号链接后的真实位置。
- 修改工具使用内容摘要阻止覆盖外部变更。
- Linux Shell 默认通过 bubblewrap，工作区可写、系统只读、临时目录独立、网络关闭。
- bubblewrap 不可用时显示警告并将 Shell 提升为审批。

### Session Store

```text
sessions/<session-id>/
├── meta.json
├── events.jsonl
└── artifacts/
```

记录包含 schema 版本、单调序号、记录 ID、父记录 ID、类型和负载。副作用执行前后分别刷盘；只有请求而无结果的操作恢复为中断且不重放。回退和分支通过父记录关系实现。损坏的尾行可丢弃，大型工具输出写入 artifacts。

### Context Engine

- 优先使用实际 token 用量，对新增内容保守估算。
- 约 80% 上下文窗口触发压缩。
- 先移除历史媒体并截断旧工具结果，再生成结构化检查点摘要。
- 保留系统指令、最近八轮交互和未闭合工具调用。
- 压缩仅改变模型视图，不删除原始会话事件。
- 摘要失败时保留原上下文并报告错误。

### 配置与项目指令

```text
内建默认值
→ ~/.config/windcode/config.toml
→ .windcode/config.toml
→ CLI/SDK 显式覆盖
```

未知字段报错，API Key 只能引用环境变量。每个目录按 `AGENTS.md → WINDCODE.md → CLAUDE.md → HERMES.md` 选择首个存在的文件，从仓库根到当前目录依次注入。

### Textual TUI

提供流式消息、可折叠工具块、内联审批/提问、状态栏、会话恢复/回退/分支选择器，以及 `/new`、`/resume`、`/rewind`、`/mode`、`/model`、`/compact`、`/status`、`/quit` 命令。

### 本地可观测性

在 XDG state 目录写入脱敏 JSONL 轨迹，记录关联 ID、耗时、token、工具状态和错误类别。默认无遥测和远程上传。

## 模块交互

```text
用户/SDK → RunRequest → Agent Runtime → Context Engine → ModelTransport
→ Tool Scheduler → Policy Engine → Sandbox/Tool → Session Store
→ Event Bus → SDK/TUI → 下一轮模型请求 → 完成报告
```

审批期间 Agent Loop 暂停对应请求但事件消费者保持运行；取消会解除等待并终止在途任务。

## 文件组织

```text
windcode/
├── pyproject.toml
├── LICENSE
├── src/windcode/
│   ├── cli.py
│   ├── sdk.py
│   ├── types.py
│   ├── domain/
│   ├── providers/
│   ├── runtime/
│   ├── tools/
│   ├── policy/
│   ├── sandbox/
│   ├── sessions/
│   ├── context/
│   ├── config/
│   ├── instructions/
│   ├── observability/
│   └── tui/
└── tests/
    ├── unit/
    ├── contract/
    ├── integration/
    └── e2e/
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 总体架构 | 分层事件内核 | 融合五种架构且保持单向依赖 |
| 公共并发模型 | 类型化异步事件流 | 匹配模型流、TUI、取消和工具进度 |
| 数据模型 | dataclass/联合类型 + Pydantic | 内核轻量，边界严格 |
| 网络层 | 官方 SDK + aiohttp | 保留原生能力并统一异步传输 |
| 会话 | 追加式事件树 | 支持恢复、审计、回退和分支 |
| 上下文 | 确定性裁剪 + 模型检查点 | 控制预算并保留语义 |
| 安全 | 策略审批 + bubblewrap | 人类控制与 OS 隔离并存 |
| 扩展 | 工具与模型传输最小协议 | 避免过早设计插件平台 |
| 可观测性 | 本地结构化轨迹 | 可诊断且保护隐私 |
| 发布 | Apache-2.0 | 宽松开源并包含专利授权 |

## 测试设计

- 单元测试覆盖事件、配置、指令、错误、预算、路径、压缩和恢复。
- 本地模拟服务覆盖三协议文本流、工具调用、用量与错误。
- 工具、安全、会话、TUI 和 SDK 分别建立集成测试。
- 端到端模拟“读取—修改—测试—报告”。
- 真实模型冒烟仅在显式提供凭据时运行。
