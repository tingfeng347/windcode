# Windcode 扩展运行时 Plan

## 架构结论

本轮在现有 Agent Loop 外增加统一的扩展控制面和运行时，不把 MCP、Skills、Hooks
或插件逻辑分别塞进 CLI、TUI、SDK。核心关系如下：

```text
CLI / TUI / SDK
       │ 只调用同一管理服务
       ▼
ExtensionService
├── DiscoveryPipeline          只读元数据，不执行扩展
├── TrustStore / StateStore    工作区信任、启禁、配置与安装记录
├── PluginInstaller            本地目录校验、摘要、原子复制
└── SnapshotBuilder ───────────────┐
                                   ▼
                         ExtensionSnapshot (不可变)
                         ├── CapabilityCatalog
                         ├── SkillCatalog
                         ├── HookPlan
                         ├── CommandCatalog
                         └── McpServerDefinitions
                                   │ 每次运行固定持有
                                   ▼
AgentLoop ── HookDispatcher ── ToolScheduler ── ToolRegistry
                                   │
                                   └── McpRuntime / McpToolAdapter
```

扩展分为两个明确阶段：

1. **解析阶段**只发现、读取有限元数据、校验、合并并生成诊断，不启动进程、不联网、
   不执行 Hook，也不读取完整 Skill 正文。
2. **激活阶段**由一次新运行持有不可变快照，按需读取 Skill、连接 MCP、执行 Hook。
   显式重载只替换 `ExtensionService` 当前快照，已经开始的运行不受影响。

这条边界同时满足安全、启动成本、故障隔离和后续插件生命周期复用需求。

## 参考实现取舍

| 来源 | 采用的设计 | 在 Windcode 中的调整 |
|---|---|---|
| Claude Code / mewcode | Skills、Hooks、MCP 和命令可组合；预工具 Hook 可拒绝；MCP Tool 进入统一工具表 | 不采用全量启动 MCP、后台失管 Hook 或执行期热重载；全部经过现有策略、沙箱、取消和事件链 |
| OpenAI Codex | 配置分层与来源绑定；Skill 根目录和插件命名空间；解析与激活分离；受限扫描；大工具集按需暴露 | 保持 Python 小内核，不复制 Rust 服务层；首版只做本地插件，不实现市场、远程同步与 OAuth |
| Pi Agent | 小型 Agent Core、稳定生命周期事件、扩展 Runner、用户命令与内部事件能力分离 | 不允许进程内任意 Python/TypeScript 扩展；只开放声明式动作和受控宿主能力 |
| GenericAgent | 常驻上下文只保留高信息密度索引，正文和大工具 Schema 按需读取 | 不在本轮引入其长期记忆与自我改写；只采用渐进披露和输出外置策略 |
| Hermes Agent | 多入口共享 Provider/运行时解析；延迟发现；故障隔离、超时、重连和统一关闭 | 将 Provider 模式收窄为 MCP 生命周期；插件不能注册宿主代码或绕过 Windcode 权限 |

源码核对重点包括：

- `/home/tingfeng/code/mewcode/mewcode/{mcp,skills,hooks}` 与工具搜索实现；
- `/home/tingfeng/code/pi-/packages/coding-agent/src/core/extensions` 的 Loader、Runner 和事件面；
- `/home/tingfeng/code/GenericAgent/plugins`、`agent_loop.py` 和按需项目记忆模式；
- OpenAI Codex 的 `core-skills`、`core-plugins`、`codex-mcp`、配置分层和 Hook Runtime；
- Hermes Agent 的 MCP、Skills、Provider Runtime、插件发现和会话清理测试。

参考实现中存在但不适合本轮的行为，例如动态导入插件代码、隐式热重载、直接继承完整
宿主环境、安装期执行脚本和自动经验写回，均不进入设计。

## 包与依赖边界

新增代码按以下方向组织：

```text
src/windcode/extensions/
├── models.py          # 来源、作用域、能力、诊断、快照等纯模型
├── paths.py           # 有界路径解析、符号链接与数据目录边界
├── discovery.py       # 分层资源发现和确定性合并
├── service.py         # CLI/TUI/SDK 共用管理服务
├── state.py           # 信任、启禁、安装来源和组件配置
├── events.py          # 扩展事件载荷构造
├── plugins/
│   ├── manifest.py    # 清单解析与版本校验
│   └── installer.py   # 本地目录安装、摘要和原子发布
├── skills/
│   ├── parser.py      # Agent Skills frontmatter 元数据解析
│   ├── loader.py      # 按需正文、引用与资源读取
│   └── tools.py       # 搜索、加载、显式调用入口
├── hooks/
│   ├── models.py      # 稳定事件、匹配器、动作和约束结果
│   ├── dispatcher.py  # 顺序、超时、隔离和递归保护
│   └── executor.py    # 通知、提示和受控命令动作
└── mcp/
    ├── client.py      # stdio / Streamable HTTP 会话
    ├── runtime.py     # 延迟连接、状态机、重连和关闭
    ├── adapter.py     # MCP Tool 到统一 Tool 语义
    └── tools.py       # 搜索、激活、Resource 和 Prompt 入口
```

依赖方向保持单向：`domain` 不依赖 `extensions`；扩展模型可引用稳定的 domain 枚举，
扩展运行时可依赖 `tools/runtime/policy/sessions`，TUI 只依赖 `ExtensionService` 的公开结果。

采用官方 Python MCP SDK实现协议和传输，不自行实现 JSON-RPC。Skill frontmatter 使用 YAML
解析器，插件清单继续用 TOML 和标准库 `tomllib`。动态 MCP JSON Schema 使用标准 JSON
Schema 校验器，不能通过不完整的临时 Pydantic 模型丢失 `oneOf`、数组或嵌套约束。

## 核心模型

### 来源与作用域

```python
class ExtensionScope(StrEnum):
    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"
    RUN = "run"


class CapabilityKind(StrEnum):
    SKILL = "skill"
    MCP_SERVER = "mcp_server"
    MCP_TOOL = "mcp_tool"
    MCP_RESOURCE = "mcp_resource"
    MCP_PROMPT = "mcp_prompt"
    HOOK = "hook"
    COMMAND = "command"
    PLUGIN = "plugin"


@dataclass(frozen=True, slots=True)
class ExtensionSource:
    scope: ExtensionScope
    path: Path | None
    plugin_id: str | None = None
    component_id: str | None = None
    digest: str | None = None
```

作用域优先级固定为 `builtin < user < project < run`。独立 Skill、Hook 和 MCP Server
使用 `(kind, public_name)` 作为覆盖键；高优先级覆盖低优先级时保留 `shadowed_by` 关系。
同一优先级重复键产生错误诊断。插件组件使用
`plugin:<plugin_id>/<kind>/<component_id>`，任何重复都报错，绝不静默覆盖。

### 诊断与状态

每个发现项均产生 `CapabilityRecord`，包含稳定 ID、显示名、类型、来源、作用域、启用、
信任、必需性、权限需求、激活状态和诊断。诊断至少包含：

- `stage`：discover、parse、validate、merge、install、activate、execute、close；
- `severity`：info、warning、error；
- `category`：格式、冲突、不兼容、信任、路径、认证、超时、协议、策略或内部错误；
- `source_id` 与脱敏后的来源标签；
- 可操作建议，不附带密钥或无限外部输出。

列表和诊断一律按 `(scope_rank, kind, public_name, source_id)` 排序，不能依赖文件系统或
并发完成顺序。

### 不可变快照

`ExtensionSnapshot` 为冻结数据，包含递增 `generation`、配置指纹、能力目录和所有已解析
定义。`ExtensionService.reload()` 先完整构建候选快照；存在必需扩展错误时保持旧快照并
返回失败，可选扩展错误则进入诊断后发布新快照。`Windcode.start_run()` 在开始时取得一个
快照引用，并基于它克隆 Tool Registry、创建 Hook Dispatcher 与 MCP Runtime。

快照中只保存路径、摘要和元数据，不保存活动 MCP 连接、异步任务或可变缓存。运行结束
统一关闭其运行级资源。

## 配置、状态与信任

### 配置分层

在现有 `AppConfig` 中增加安全默认关闭的 `extensions` 段：

```toml
[extensions]
enabled = true
direct_tool_limit = 24
connect_timeout_seconds = 10
call_timeout_seconds = 60
hook_timeout_seconds = 10
max_metadata_bytes = 65536
max_content_bytes = 1048576

skill_roots = ["~/.claude/skills", "~/.codex/skills", "~/.pi/agent/skills"]

[extensions.mcp_servers.example]
transport = "stdio"
command = "example-mcp"
args = []
required = false
env = { TOKEN = { env = "EXAMPLE_TOKEN" } }
```

未出现 `extensions` 或 `enabled = false` 时不扫描额外目录、不创建扩展服务后台任务、
不启动进程且不联网。兼容目录只在用户显式配置后扫描，避免读取无关用户数据。

项目配置仍从 `.windcode/config.toml` 进入现有配置层，但项目扩展定义在工作区未受信任时
只作为待检查来源，不能进入激活快照。运行级资源由 SDK/CLI 显式参数传入，不持久化。

### 信任与持久状态

新增用户数据文件：

```text
<user_data>/windcode/extensions/state.json
<user_data>/windcode/extensions/plugins/<plugin-id>/<content-digest>/
<user_data>/windcode/extensions/plugin-data/<plugin-id>/
```

`state.json` 原子写入并使用用户私有权限，记录：

- 规范化工作区身份和用户明确授予的信任；
- 已安装插件 ID、源目录标签、内容摘要、安装时间和当前版本；
- 用户级启用/禁用状态及非敏感配置；
- 不存储认证头或密钥值。

工作区信任以规范化工作区根路径和当前文件系统身份生成稳定键。符号链接别名不能产生
第二份信任；路径指向不同对象后必须重新信任。信任只是允许加载项目扩展，不代表批准
工具副作用。

密钥只允许 `{ env = "NAME" }` 或 `{ credential = "id" }` 引用。解析值仅存在于连接
创建的局部生命周期，并立即加入 Redactor；普通配置中看似密钥的明文字段直接校验失败。

## 插件清单与本地安装

插件根目录使用 `.windcode-plugin/plugin.toml`：

```toml
manifest_version = 1
id = "com.example.review"
name = "Review Helper"
version = "1.0.0"
windcode = ">=0.1,<0.2"
required = false

skills = [{ id = "review", path = "skills/review" }]
hooks = [{ id = "guard", path = "hooks/guard.toml" }]
commands = [{ name = "review", target = "skill:review" }]

[mcp_servers.analysis]
path = "mcp/analysis.toml"

[permissions]
effects = ["read", "process"]
network_hosts = []

[data]
persistent = true
```

解析器拒绝未知的必需字段、非法 ID、重复组件、绝对路径、`..`、根目录外符号链接、设备
文件、过大元数据和不兼容版本。所有相对路径通过同一个 `resolve_beneath()` 解析；校验时
同时检查词法路径、规范路径和最终文件类型。

本地安装流程：

1. 只读清单和声明的元数据文件，完成完整校验；
2. 按规范化相对路径、文件类型和内容计算确定性 SHA-256，忽略缓存与 VCS 元数据；
3. 将允许的文件复制到同一文件系统的临时目录，每个文件再次做边界和摘要检查；
4. `fsync` 后原子发布到 `<plugin-id>/<digest>`；
5. 原子更新状态文件，默认保持禁用，除非用户在同一显式操作中要求启用；
6. 安装全程不启动 MCP、不执行 Hook/脚本、不读取 Skill 辅助正文。

首版“禁用”只影响新快照；不实现完整卸载。重复安装相同摘要幂等，不同内容但同版本
给出明确诊断。插件数据目录由宿主注入，清单不能指定任意持久路径。

声明式命令的 `target` 仅允许 `skill:<id>`、`prompt:<id>` 或内建能力 ID。命令模型不含
`command`、`shell`、模块或入口点字段，因而不能成为任意宿主执行入口。

## Agent Skills

### 发现

Skill 目录以 `SKILL.md` 为入口，frontmatter 至少包含 `name` 和 `description`。发现阶段
只读取受限字节数的 frontmatter，不读正文。扫描具有固定最大深度、目录数、条目数和
并发数；默认不跟随目录符号链接。来源包括：

- Windcode 内建 Skill；
- 用户显式配置的 Windcode、Claude Code、Codex、Pi Skill 根目录；
- 受信任工作区的 `.windcode/skills`；
- 插件清单声明的 Skill；
- 本次运行显式传入的 Skill 根目录。

名称、描述、长度、编码、frontmatter 类型和入口路径均严格校验。覆盖规则由统一目录
处理，不由各兼容 Loader 自行决定。

### 渐进披露

系统提示只渲染稳定排序的名称、短描述、来源标签和显式调用方式。`search_skills` 返回
精简匹配结果；`load_skill` 在匹配或用户显式 `$name`/命令调用后才读取完整正文。

Skill Loader 返回带来源的 `SkillContent`，而不是裸字符串。它解析正文直接引用的文件，
但只在调用方请求具体引用时读取；所有引用必须保持在 Skill 根目录内并经过大小、文件
类型和符号链接检查。脚本仅作为可读资源或由现有 Shell Tool 显式执行，不因加载 Skill
自动运行。

每次运行缓存按 `(source_id, digest)` 隔离。磁盘变化不会改变当前运行；重载后新快照才
看到变化。

## MCP 运行时

### Server 定义与状态机

```text
DISCOVERED ──activate──> CONNECTING ──> READY
                         │              │
                         ├──> FAILED <──┤ protocol/transport error
                         │              └──> CLOSING ──> CLOSED
                         └──cancel───────────────> CLOSED
```

`McpRuntime` 属于一次 Windcode 运行。默认不连接任何 Server；只有需要其 Instructions、
首次列举能力、读取 Resource/Prompt 或调用 Tool 时才激活。必需且声明为启动前需要的 Server
可在运行准备阶段以有界并发连接，失败则阻止该运行。可选 Server 独立失败并保留诊断。

每个连接使用独立锁避免重复启动，连接、初始化、列举、调用和关闭均有超时与取消。
stdio 进程只继承最小环境加声明的变量；stderr 有界捕获并脱敏。Streamable HTTP 只接受
显式 URL 和静态头引用，遵守现有网络权限上限。关闭顺序为停止新调用、取消在途请求、
关闭 MCP Session/HTTP Client、终止并等待子进程，超时后强制结束并确认回收。

失败恢复只允许有界的一次惰性重连，不无限重试。取消、认证错误和策略拒绝不重连。

### 能力映射

MCP Server 名称先规范化，能力稳定 ID 使用：

```text
mcp:<server-id>/tool/<tool-name>
mcp:<server-id>/resource/<uri-or-template-id>
mcp:<server-id>/prompt/<prompt-name>
```

MCP Tool 通过 `McpToolAdapter` 进入现有 Tool Registry、Scheduler 和 Policy Engine。
为保留完整 JSON Schema，统一 Tool 接口增加可选的原始 `input_schema` 和
`validate_arguments()`；现有 Pydantic Tool 由默认适配器保持行为不变。

未知 MCP Tool 的 effects 固定至少包含外部副作用标记并触发审批。Server 注解只显示给
用户，不减少 effects。用户按 Server/Tool 配置的策略只允许收紧或在当前 PermissionMode
允许范围内缓存审批，不能直接返回高于父策略的 `ALLOW`。

MCP 结果统一转换为 `ToolResult`：文本、结构化内容和资源链接保留类型摘要；图片或二进制
进入 ArtifactStore；错误、参数错误、远端取消和本地取消使用稳定错误类别。输出先脱敏，
超过阈值的完整内容写 artifact，模型和终端只接收摘要引用。

Resources 和 Prompts 通过内建受控工具 `list_mcp_resources`、`read_mcp_resource`、
`list_mcp_prompts`、`get_mcp_prompt` 按需访问。Server Instructions 有单独大小上限、来源
边界和提示注入标签，不能混入核心 system prompt 文本而失去来源。

### 大工具集

构建运行工具表时：

- MCP Tool 总量不超过 `direct_tool_limit`：注册完整 Tool Schema；
- 超过阈值：只注册 `search_mcp_tools` 和已激活工具，系统上下文保留精简目录；
- 搜索命中或明确 `select:<stable-id>` 后，把完整 Schema 加入该运行的动态工具视图；
- 动态激活只改变当前运行内部的“已暴露集合”，不改变全局快照。

Agent Loop 每轮从运行级 Tool View 取 Schema，使新激活工具从下一模型步可见。完整目录不
写入对话历史，避免上下文线性增长。

## Hooks

### 稳定事件面

首版公开以下高层事件，不暴露模型 token 增量和底层存储回调：

```text
session_start / session_end
user_submit
run_start / run_end / run_error
tool_before_policy / tool_after
permission_request
compact_before / compact_after
subagent_start / subagent_end
```

每个 `HookContext` 是冻结的、版本化的最小载荷，带 session/run/source/correlation ID，
工具参数和输出使用已有脱敏及截断策略。Hook 匹配只允许事件、工具稳定 ID、状态和有限
字段条件，不执行表达式语言。

### 动作与顺序

允许动作：

- `notify`：产生结构化通知；
- `prompt`：把带 Hook 来源和生命周期的有界上下文排入下一模型步；
- `command`：通过已有 Shell Tool、Policy Engine、Sandbox、超时和输出边界执行；
- `reject` / `tighten`：仅限预执行事件，拒绝操作或增加 effects/路径/网络限制。

工具调用顺序调整为：

```text
参数校验
  -> tool_before_policy Hooks
  -> 合并只增不减的约束
  -> PolicyEngine
  -> permission_request Hooks（只观察或收紧）
  -> 用户审批
  -> 持久化 ToolStarted
  -> 执行工具
  -> 持久化 ToolFinished
  -> tool_after Hooks
```

当前 Scheduler 的 `before_execute` 位于策略判定后，需拆成 `before_policy`、
`before_execute` 和 `after_execute` 三个明确扩展点。Hook 拒绝必须发生在副作用和
`ToolStarted` 之前；Hook 失败不能把拒绝变为允许。

Hook 命令创建带 `origin=hook:<id>` 的内部 ScheduledCall，并禁止再次触发会造成递归的
同源 Hook。每个 Hook 有固定超时、输出上限和单次执行计数；首版不重试。同步预 Hook
按稳定优先级串行执行，通知型后置 Hook 可有界并发，但运行结束前必须收敛或取消。

后置 Hook 不能替换历史 ToolResult。其 prompt 动作只作为新的
`SourcedContextMessage` 加入后续上下文，并同时持久化来源。

## 事件、持久化与故障隔离

扩展事件沿用 `EventBus.publish()` 的“先追加 SessionStore、后通知消费者”语义。新增事件
至少覆盖：发现完成、诊断、快照重载、能力激活、MCP 连接/调用/关闭、Skill 加载、Hook
开始/结束/拒绝和插件状态变化。

事件包含 `extension_id`、`source_id`、`snapshot_generation`，MCP/Hook 事件再包含
`server_id` 或 `hook_id` 与原工具 `call_id`。新增事件保持可忽略，旧会话反序列化不要求
认识所有类型。

故障边界如下：

- 单个文件解析失败只污染对应能力；
- 单个可选插件或 MCP Server 失败不阻止其他能力；
- 必需组件失败只终止相关重载或运行，并保留明确诊断；
- Hook 异常按动作安全语义处理：观察型记录失败，安全判定型 fail closed；
- 所有后台任务归属于运行级 `AsyncExitStack`/TaskGroup，SDK 关闭时统一收敛；
- 外部完整输出只能进入本地 artifact，不进入事件、错误或模型上下文。

## 公共管理接口

`ExtensionService` 提供唯一状态转换实现：

```python
async def list_capabilities() -> tuple[CapabilityRecord, ...]: ...
async def inspect(source_id: str) -> ExtensionInspection: ...
async def install_local(path: Path, *, enable: bool = False) -> InstallResult: ...
async def set_enabled(extension_id: str, enabled: bool) -> ReloadRequired: ...
async def trust_workspace(workspace: Path, trusted: bool) -> ReloadRequired: ...
async def reload() -> ReloadResult: ...
```

SDK 在 `Windcode` 上暴露该服务的类型化方法。CLI 增加非交互子命令用于自动化：

```text
windcode extensions list|inspect|install|enable|disable|reload|trust
```

现有 `windcode [workspace]` 启动语义不变。TUI 增加 `/extensions` 管理视图，安装与信任等
有状态操作显示来源、权限和确认；插件声明式命令合并进同一命令目录并保留插件来源。
三个入口只格式化服务返回值，不自行扫描或写状态。

## 对现有运行时的改造点

- `config/models.py`、`loader.py`：新增严格扩展配置和安全默认值；
- `domain/tools.py`、`tools/registry.py`：支持原始 JSON Schema、统一参数验证和动态 Tool View；
- `runtime/scheduler.py`：增加策略前、执行前、执行后拦截点及来源信息；
- `runtime/loop.py`：持有运行快照、动态工具视图、带来源上下文和完整 Hook 生命周期；
- `runtime/event_bus.py`、`domain/events.py`：新增可关联扩展事件并保持先持久化语义；
- `runtime/prompts.py`：只注入精简 Skills/MCP 目录和明确来源；
- `sdk.py`：组装 ExtensionService，运行级 MCP/Hook 资源，并确保先关闭运行再关闭服务；
- `sessions/artifacts.py`、`observability/redaction.py`：复用外部输出外置和动态敏感值脱敏；
- `cli.py`、`tui/commands.py`、`tui/app.py`：接入同一管理服务；
- `types.py`：导出稳定的扩展记录、诊断、安装和重载结果类型。

在未启用扩展时，`start_run()` 仍走当前工具注册、提示构建和 Agent Loop 路径，只多取得
一个空快照常量，不创建网络客户端或子进程。

## 实施阶段

### 阶段 1：基础模型、配置、信任与发现

建立扩展纯模型、路径安全、状态存储、确定性分层合并和空快照，先锁住安全不变量与
无扩展兼容行为。完成 Skill frontmatter 和插件清单的只读解析。

### 阶段 2：插件本地安装与统一服务

实现内容摘要、原子本地安装、启禁、工作区信任、显式重载和不可变快照。接入 SDK 与
CLI 的非交互管理接口，再让 TUI 消费同一服务。

### 阶段 3：Skills 与渐进披露

实现兼容根发现、冲突规则、正文/引用按需加载、来源标记、Skill 搜索和命令路由。验证
初始上下文不包含正文和引用资源。

### 阶段 4：MCP 协议与工具适配

接入官方 SDK，先完成 stdio，再完成 Streamable HTTP；随后实现 Tools、Resources、
Prompts、Instructions、认证引用、动态 Schema、审批、输出外置和大工具集按需激活。

### 阶段 5：Hooks 与运行时交叉点

先改 Scheduler 的安全顺序，再接稳定生命周期事件、通知/提示动作，最后接受控命令、
拒绝/收紧、递归保护和子智能体生命周期。Hook 和 MCP 共享同一取消及关闭树。

### 阶段 6：入口一致性、端到端与回归

完成 TUI 管理体验、插件声明式命令、结构化审计与完整本地插件端到端场景。最后验证
旧配置、旧会话、单智能体、多智能体、无扩展启动和构建全部保持兼容。

## 测试策略

### 单元测试

- 清单、Skill frontmatter、密钥引用和扩展配置的严格校验；
- 路径逃逸、符号链接越界、设备文件、大小限制和插件数据隔离；
- 作用域覆盖、同层冲突、插件命名空间和稳定排序；
- 快照代次、失败回滚和运行持有旧快照；
- Hook 匹配、只收紧合并、fail-closed、递归保护和超时；
- MCP JSON Schema 参数验证、effects、结果转换和动态工具暴露；
- 内容摘要、幂等安装、原子状态写入和脱敏。

### 契约测试

在 `tests/contract/mcp/` 提供本地确定性 Server：

- stdio Server 覆盖初始化、Instructions、Tools、Resources、Prompts、取消和崩溃；
- aiohttp Streamable HTTP Server 覆盖请求头引用、协议错误、超时和连接关闭；
- 所有测试禁用外部网络，不需要模型密钥；
- 验证 SDK 协议对象到 Windcode ToolResult/Event 的稳定映射。

### 集成测试

- 未信任项目资源只可检查，信任并重载后仅新运行可激活；
- MCP Tool 复用 Policy Engine、审批、Scheduler、ArtifactStore、取消和审计；
- Hook 拒绝发生在副作用前，Hook 命令走沙箱和审批；
- Skill 正文和 MCP 大 Schema 只在激活后进入下一模型请求；
- 可选扩展失败隔离，必需扩展失败明确终止；
- CLI、TUI、SDK 对相同操作观察到相同状态和事件；
- SDK 关闭和取消后无子进程、连接、任务或审批等待泄漏。

### 端到端场景

构造一个本地插件，包含一个 Agent Skill、一个 stdio MCP Server、工具前后 Hooks 和一个
声明式命令。测试从本地安装、检查、信任、启用和重载开始，经 TUI 命令触发 Skill、
按需调用 MCP Tool、观察 Hook 拒绝及通知，再通过 SDK 禁用和重载，确认旧运行保持原
快照、新运行不再可见该插件且审计时间线完整。

### 完整质量门槛

最终依次运行：

```text
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
uv build
git diff --check
```

额外记录无扩展启动探针：没有新增子进程、网络连接或用户兼容目录扫描。真实远程 MCP
和第三方插件只放在显式启用的 smoke tests。

## 关键风险与控制

- **扩展面过宽**：首版只允许声明式插件和受控动作，禁止动态宿主代码。
- **MCP SDK 语义变化**：封装在 `extensions/mcp/client.py`，以本地契约测试锁定边界。
- **动态 Schema 破坏现有工具接口**：先引入兼容验证抽象，所有内建 Tool 回归通过后再接 MCP。
- **Hook 形成权限旁路**：策略前只允许拒绝/收紧，命令复用 Scheduler，后置动作不能改结果。
- **重载竞态**：构建候选后原子交换快照，运行只持有固定 generation。
- **安装期供应链风险**：只读元数据、内容寻址、原子复制，不执行任何扩展内容。
- **上下文膨胀**：Skill 与 MCP 都采用精简目录、显式搜索和运行级按需激活。
- **资源泄漏**：所有活动资源归属运行级关闭树，取消与退出测试检查实际进程和任务。

## 后续 Spec 边界

本计划只预留 `PluginSource`、内容摘要、稳定插件 ID、数据目录和 Provider 接口。Git/URL/
市场安装、锁文件、更新、回滚、完整卸载和依赖解析必须进入第二个 Spec。

长期记忆只可在未来作为新的受控 Provider 消费稳定事件和来源上下文；用户画像、经验
结晶、自我进化、自动 Skill 修改和后台反思必须进入第三个 Spec，不得借 Hook 或插件
数据目录在本轮提前实现。
