# Windcode 扩展运行时 Tasks

## 文件清单

| 操作 | 文件或目录 | 职责 |
|---|---|---|
| 修改 | `pyproject.toml`、`uv.lock` | 引入官方 MCP SDK、YAML frontmatter 与 JSON Schema 校验依赖 |
| 新建 | `src/windcode/extensions/` | 扩展模型、路径安全、发现、状态、事件、快照与统一服务 |
| 新建 | `src/windcode/extensions/plugins/` | 声明式插件清单解析和本地原子安装 |
| 新建 | `src/windcode/extensions/skills/` | Agent Skills 元数据发现、按需内容加载和工具入口 |
| 新建 | `src/windcode/extensions/mcp/` | MCP 传输、生命周期、能力发现和统一工具适配 |
| 新建 | `src/windcode/extensions/hooks/` | Hook 模型、匹配、调度和受控动作执行 |
| 修改 | `src/windcode/config/` | 扩展配置、密钥引用、安全默认值与分层加载 |
| 修改 | `src/windcode/domain/` | 原始 JSON Schema、带来源上下文和扩展事件 |
| 修改 | `src/windcode/runtime/` | 动态工具视图、Hook 交叉点、运行快照与关闭树 |
| 修改 | `src/windcode/tools/` | 工具参数验证抽象和扩展内建工具注册 |
| 修改 | `src/windcode/policy/`、`src/windcode/sandbox/` | MCP 与 Hook 的权限上限、网络和进程边界 |
| 修改 | `src/windcode/sessions/`、`src/windcode/observability/` | 外部大输出、事件持久化和动态敏感值脱敏 |
| 修改 | `src/windcode/sdk.py`、`src/windcode/types.py` | 公共扩展管理接口、运行组装和稳定类型导出 |
| 修改 | `src/windcode/cli.py` | 非交互扩展管理子命令 |
| 修改 | `src/windcode/tui/` | `/extensions` 管理视图和插件声明式命令 |
| 新建 | `tests/unit/extensions/` | 配置、路径、发现、安装、Skills、MCP 与 Hooks 单元测试 |
| 新建 | `tests/contract/mcp/` | 本地 stdio 与 Streamable HTTP MCP 契约测试 |
| 新建 | `tests/integration/test_extensions_*.py` | 信任、快照、策略、生命周期和入口一致性测试 |
| 新建 | `tests/e2e/test_extension_runtime.py` | 完整本地插件端到端场景 |
| 修改 | 既有测试 | 无扩展、旧配置、旧会话、单智能体和多智能体回归 |

## T1：锁定扩展协议依赖

**文件：** `pyproject.toml`、`uv.lock`
**依赖：** 无

**步骤：**
1. 增加官方 Python MCP SDK、YAML frontmatter 解析器和 JSON Schema 校验器。
2. 将依赖版本锁定到支持 stdio 与 Streamable HTTP 的兼容范围。
3. 确认依赖导入不产生网络连接、后台任务或全局状态。
4. 更新锁文件并验证冻结安装。

**验证：** 运行 `uv sync --frozen --all-groups` 和最小导入探针，期望均退出码为 0。

## T2：定义扩展配置与安全默认值

**文件：** `src/windcode/config/models.py`、`src/windcode/config/loader.py`、`src/windcode/config/writer.py`、`tests/unit/extensions/test_config.py`
**依赖：** T1

**步骤：**
1. 定义扩展开关、扫描上限、超时、直接工具阈值、Skill 根和 MCP Server 配置。
2. 定义环境变量与外部凭据引用，拒绝普通配置中的明文密钥和认证头。
3. 保持旧配置缺少 `extensions` 时可读取，默认不扫描、不连接和不启动进程。
4. 实现用户、项目和运行级配置的确定性合并，项目层不能放宽运行级限制。
5. 添加严格字段、边界值、旧配置和序列化往返测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_config.py tests/unit/test_config_loader.py tests/unit/test_config_models.py`，期望全部通过。

## T3：定义扩展领域模型和不可变快照

**文件：** `src/windcode/extensions/__init__.py`、`src/windcode/extensions/models.py`、`tests/unit/extensions/test_models.py`
**依赖：** 无

**步骤：**
1. 定义作用域、能力类型、来源、诊断阶段、严重级别和激活状态。
2. 定义稳定能力 ID、覆盖关系、权限需求、检查结果和管理操作结果。
3. 定义冻结的 `ExtensionSnapshot`，包含 generation、配置指纹和只读能力定义。
4. 规定所有公开集合的稳定排序及插件命名空间格式。
5. 添加不可变性、标识规范化、排序和序列化测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_models.py`，期望全部通过。

## T4：实现有界路径解析

**文件：** `src/windcode/extensions/paths.py`、`tests/unit/extensions/test_paths.py`
**依赖：** T3

**步骤：**
1. 实现 `resolve_beneath()`，同时检查词法路径、规范路径和最终文件类型。
2. 拒绝绝对路径、`..`、符号链接越界、设备文件、FIFO 和跨根访问。
3. 实现有界文件读取、最大深度、最大条目数和默认不跟随目录符号链接的扫描器。
4. 为插件数据目录生成宿主管理路径，禁止清单指定任意持久目录。
5. 添加路径逃逸、符号链接替换、特殊文件、大小限制和正常嵌套路径测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_paths.py`，期望全部通过。

## T5：实现扩展状态与工作区信任存储

**文件：** `src/windcode/extensions/state.py`、`tests/unit/extensions/test_state.py`
**依赖：** T3、T4

**步骤：**
1. 定义安装记录、启禁状态、非敏感配置和工作区信任记录。
2. 使用规范化工作区根和文件系统身份生成信任键，符号链接别名不得重复授权。
3. 以用户私有权限、临时文件、`fsync` 和原子替换写入 `state.json`。
4. 对损坏状态返回结构化诊断，不静默重置或覆盖用户数据。
5. 添加原子写入、权限、别名、路径对象变化、损坏恢复和密钥缺失测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_state.py`，期望全部通过。

## T6：解析并校验声明式插件清单

**文件：** `src/windcode/extensions/plugins/__init__.py`、`src/windcode/extensions/plugins/manifest.py`、`tests/unit/extensions/test_plugin_manifest.py`
**依赖：** T3、T4

**步骤：**
1. 解析 `.windcode-plugin/plugin.toml` 的版本、插件元数据、兼容范围和必需性。
2. 解析 Skills、MCP Servers、Hooks、命令、权限和数据需求，并建立插件命名空间。
3. 拒绝非法或重复 ID、未知必需字段、不兼容版本和所有越界组件路径。
4. 将命令目标限制为声明式提示、Skill 或已有能力 ID，模型中不提供任意代码入口字段。
5. 添加有效组合清单和各类恶意清单测试，确认解析阶段不执行任何插件内容。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_plugin_manifest.py`，期望全部通过。

## T7：解析 Agent Skill 元数据

**文件：** `src/windcode/extensions/skills/__init__.py`、`src/windcode/extensions/skills/parser.py`、`tests/unit/extensions/test_skill_parser.py`
**依赖：** T3、T4

**步骤：**
1. 只读取 `SKILL.md` 的有界 frontmatter，要求有效的名称和短描述。
2. 兼容 Agent Skills 规范及显式配置的 Claude Code、Codex 和 Pi 常见目录布局。
3. 校验编码、字段类型、名称、描述长度、入口文件和元数据大小。
4. 发现阶段不得读取正文、引用、脚本或资源。
5. 添加读取计数测试，证明元数据发现没有展开正文和辅助文件。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_skill_parser.py`，期望全部通过。

## T8：实现确定性扩展发现与合并

**文件：** `src/windcode/extensions/discovery.py`、`tests/unit/extensions/test_discovery.py`
**依赖：** T2、T3、T5、T6、T7

**步骤：**
1. 按内建、用户、项目、运行级顺序发现独立资源和已安装插件元数据。
2. 对未信任项目资源只生成可检查记录，不将其放入激活候选。
3. 实现独立资源的高层覆盖、同层冲突和插件组件绝不静默覆盖规则。
4. 隔离单文件与单插件解析错误，按必需性决定诊断而非中断全部扫描。
5. 固定扫描、记录、覆盖和诊断顺序，添加随机遍历顺序重复运行测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_discovery.py`，期望全部通过。

## T9：构建候选快照并原子发布

**文件：** `src/windcode/extensions/snapshot.py`、`tests/unit/extensions/test_snapshot.py`
**依赖：** T8

**步骤：**
1. 从发现结果构建仅含元数据和定义的冻结快照，不持有活动连接或可变缓存。
2. 生成稳定配置指纹和递增 generation。
3. 可选扩展错误进入诊断后仍可发布，必需扩展错误阻止发布并保留旧快照。
4. 以单次原子引用交换发布新快照，读者无需持有写锁。
5. 添加失败回滚、并发读取、generation 和旧快照持续可用测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_snapshot.py`，期望全部通过。

## T10：增加扩展结构化事件

**文件：** `src/windcode/domain/events.py`、`src/windcode/extensions/events.py`、`tests/unit/test_events.py`、`tests/unit/extensions/test_events.py`
**依赖：** T3

**步骤：**
1. 增加发现、诊断、重载、激活、Skill、MCP、Hook 和插件状态事件。
2. 所有事件携带 snapshot generation、extension/source ID 和关联运行信息。
3. MCP 与 Hook 事件额外携带 server/hook ID 和相关工具 call ID。
4. 保持旧消费者可忽略未知事件，旧会话反序列化无需迁移。
5. 添加新事件往返、排序、来源字段和旧事件兼容测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_events.py tests/unit/extensions/test_events.py`，期望全部通过。

## T11：实现统一扩展管理服务

**文件：** `src/windcode/extensions/service.py`、`tests/unit/extensions/test_service.py`
**依赖：** T5、T8、T9、T10

**步骤：**
1. 实现列出、检查、启用、禁用、信任和显式重载的唯一状态转换逻辑。
2. 管理服务只持有状态和当前快照，不启动 MCP 或运行 Hook。
3. 状态变化返回 `reload_required`，不得暗中改变已开始运行。
4. 重载先完整构建候选，再持久化事件并发布结果。
5. 添加幂等操作、失败回滚、并发重载和无扩展空快照测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_service.py`，期望全部通过。

## T12：实现本地插件原子安装

**文件：** `src/windcode/extensions/plugins/installer.py`、`src/windcode/extensions/service.py`、`tests/unit/extensions/test_plugin_installer.py`
**依赖：** T4、T5、T6、T11

**步骤：**
1. 安装前只读并完整校验清单及声明的元数据。
2. 对规范化相对路径和内容计算确定性 SHA-256，忽略 VCS 与缓存元数据。
3. 复制到同文件系统临时目录，每个文件再次验证边界和摘要后原子发布。
4. 相同摘要安装保持幂等；同版本不同内容产生明确诊断；默认安装后禁用。
5. 通过进程和文件访问探针证明安装不启动 MCP、不执行 Hook/脚本、不展开 Skill 正文。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_plugin_installer.py`，期望全部通过。

## T13：导出 SDK 扩展管理接口

**文件：** `src/windcode/sdk.py`、`src/windcode/types.py`、`tests/integration/test_sdk.py`、`tests/integration/test_extensions_sdk.py`
**依赖：** T11、T12

**步骤：**
1. 在 `Windcode` 上组装单个 `ExtensionService` 并暴露类型化管理方法。
2. 导出能力、诊断、检查、安装、信任和重载结果类型。
3. 保持现有 `start_run()`、`RunHandle` 和关闭接口兼容。
4. 多个并发运行只读取快照，不共享后续运行级可变资源。
5. 添加公共导入、管理状态转换和两个 SDK 实例隔离测试。

**验证：** 运行 `uv run pytest -q tests/integration/test_extensions_sdk.py tests/integration/test_sdk.py -k 'extension or public'`，期望全部通过。

## T14：增加 CLI 扩展管理子命令

**文件：** `src/windcode/cli.py`、`tests/unit/test_cli.py`、`tests/integration/test_extensions_cli.py`
**依赖：** T13

**步骤：**
1. 增加 `extensions list|inspect|install|enable|disable|reload|trust` 非交互子命令。
2. 所有命令调用 SDK 管理接口，不自行扫描或写状态文件。
3. 输出稳定的来源、作用域、启用、信任、权限和诊断信息，并提供机器可解析模式。
4. 有状态操作要求显式参数，错误码区分参数、校验、信任和重载失败。
5. 保持 `windcode [workspace]` 原启动语义不变。

**验证：** 运行 `uv run pytest -q tests/unit/test_cli.py tests/integration/test_extensions_cli.py`，期望全部通过。

## T15：实现 Skill 正文与引用按需加载

**文件：** `src/windcode/extensions/skills/loader.py`、`tests/unit/extensions/test_skill_loader.py`
**依赖：** T4、T7、T9

**步骤：**
1. 通过快照中的 source ID 按需读取完整 `SKILL.md`，返回带来源的 `SkillContent`。
2. 只在调用方请求具体引用时读取辅助文件，所有读取复用有界路径规则。
3. 将脚本视为可读资源，不因 Skill 加载自动执行。
4. 按 `(source_id, digest)` 建立运行级缓存，磁盘变化不改变当前运行视图。
5. 添加越界引用、超大内容、磁盘变化、来源隔离和读取计数测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_skill_loader.py`，期望全部通过。

## T16：实现 Skill 搜索与显式加载工具

**文件：** `src/windcode/extensions/skills/tools.py`、`src/windcode/tools/builtins.py`、`tests/unit/extensions/test_skill_tools.py`
**依赖：** T15

**步骤：**
1. 实现只返回名称、短描述和来源的 `search_skills`。
2. 实现按稳定 ID 或 `$name` 显式调用的 `load_skill`。
3. 对同名覆盖显示胜出项和被覆盖来源，同层冲突不得加载。
4. 将 Skill 内容作为带来源上下文排入下一模型步，而非伪造用户消息。
5. 添加搜索排序、显式选择、冲突、未信任和内容来源测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_skill_tools.py`，期望全部通过。

## T17：渲染精简 Skill 目录与声明式命令

**文件：** `src/windcode/runtime/prompts.py`、`src/windcode/tui/commands.py`、`src/windcode/extensions/commands.py`、`tests/unit/test_prompts.py`、`tests/unit/extensions/test_commands.py`
**依赖：** T6、T9、T16

**步骤：**
1. 系统上下文只渲染稳定排序的 Skill 名称、短描述、来源和调用方式。
2. 插件命令只路由到 Skill、MCP Prompt 或已有能力入口。
3. 命令冲突遵循插件命名空间和统一诊断，不静默覆盖内建命令。
4. 证明初始模型请求不含 Skill 正文、引用文件或脚本内容。
5. 添加 `$name`、斜杠命令、来源显示和上下文体积测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_prompts.py tests/unit/extensions/test_commands.py`，期望全部通过。

## T18：扩展统一工具参数验证接口

**文件：** `src/windcode/domain/tools.py`、`src/windcode/tools/registry.py`、`src/windcode/runtime/loop.py`、`tests/unit/test_tool_registry.py`、`tests/unit/tools/test_builtins.py`
**依赖：** T1

**步骤：**
1. 为工具增加可选原始 `input_schema` 和统一 `validate_arguments()` 接口。
2. 现有 Pydantic 工具通过默认适配保持错误格式和执行行为不变。
3. 使用完整 JSON Schema 支持嵌套对象、数组、联合约束和附加属性规则。
4. Registry 克隆与模型工具序列化均保留原始 Schema。
5. 添加复杂 Schema、错误路径和全部内建工具回归测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_tool_registry.py tests/unit/tools/test_builtins.py`，期望全部通过。

## T19：实现 MCP stdio 客户端

**文件：** `src/windcode/extensions/mcp/__init__.py`、`src/windcode/extensions/mcp/client.py`、`tests/contract/mcp/stdio_server.py`、`tests/contract/mcp/test_stdio_client.py`
**依赖：** T1、T2、T4

**步骤：**
1. 使用官方 SDK 建立 stdio 会话、初始化协商和能力调用。
2. 子进程只继承最小环境及声明的变量，stderr 有界捕获并脱敏。
3. 为启动、初始化、请求、取消和关闭设置明确超时。
4. 关闭时先停止新请求，再取消在途调用、关闭会话并确认子进程回收。
5. 添加正常、慢启动、崩溃、协议错误、取消和强制关闭契约测试。

**验证：** 运行 `uv run pytest -q tests/contract/mcp/test_stdio_client.py`，期望全部通过且无遗留子进程。

## T20：实现 MCP Streamable HTTP 客户端

**文件：** `src/windcode/extensions/mcp/client.py`、`tests/contract/mcp/http_server.py`、`tests/contract/mcp/test_http_client.py`
**依赖：** T19

**步骤：**
1. 使用官方 SDK 和现有 aiohttp 栈建立 Streamable HTTP 会话。
2. 只接受显式 URL 与静态请求头引用，认证值解析后立即注册到 Redactor。
3. 在建立连接前应用现有网络权限上限和允许主机约束。
4. 明确处理状态码、协议错误、超时、服务端关闭和本地取消。
5. 添加头引用、密钥脱敏、慢响应、错误响应和连接回收测试。

**验证：** 运行 `uv run pytest -q tests/contract/mcp/test_http_client.py`，期望全部通过且测试不访问外部网络。

## T21：实现运行级 MCP 生命周期

**文件：** `src/windcode/extensions/mcp/runtime.py`、`tests/unit/extensions/test_mcp_runtime.py`
**依赖：** T9、T19、T20

**步骤：**
1. 实现 discovered、connecting、ready、failed、closing、closed 状态机。
2. 每个 Server 使用独立锁防止重复启动，默认直到首次使用才连接。
3. 必需预启动 Server 以有界并发激活；可选 Server 故障彼此隔离。
4. 仅对可恢复传输故障进行一次惰性重连，取消、认证和策略拒绝不重试。
5. 所有连接和后台任务归属运行级关闭树，并添加并发激活与关闭竞态测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_mcp_runtime.py`，期望全部通过。

## T22：发现 MCP Tools、Resources、Prompts 与 Instructions

**文件：** `src/windcode/extensions/mcp/runtime.py`、`src/windcode/extensions/mcp/catalog.py`、`tests/contract/mcp/test_capabilities.py`
**依赖：** T21

**步骤：**
1. 发现四类 MCP 能力并生成带 Server 来源的稳定 ID。
2. 校验名称、URI、模板、Schema、描述和 Instructions 大小与格式。
3. 保留 Server 风险注解用于展示，但不将其转换为信任或授权。
4. 单类能力无效只生成对应诊断，不破坏其他有效能力。
5. 添加能力齐全、分页、空能力、重复名称和恶意超大元数据测试。

**验证：** 运行 `uv run pytest -q tests/contract/mcp/test_capabilities.py`，期望全部通过。

## T23：适配 MCP Tool 与结构化结果

**文件：** `src/windcode/extensions/mcp/adapter.py`、`src/windcode/sessions/artifacts.py`、`tests/unit/extensions/test_mcp_adapter.py`
**依赖：** T18、T21、T22

**步骤：**
1. 将 MCP Tool 的原始 JSON Schema、来源和稳定 ID 适配到统一 Tool 接口。
2. 转换文本、结构化内容、资源链接、图片、二进制、错误和取消结果。
3. 图片、二进制和超大完整输出进入 ArtifactStore，模型与终端只接收有界摘要引用。
4. 参数错误、远端错误和本地取消使用稳定错误类别。
5. 添加复杂参数、混合内容、超大输出、脱敏和 artifact 往返测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_mcp_adapter.py tests/unit/test_artifacts.py`，期望全部通过。

## T24：接入 MCP 工具权限与审批

**文件：** `src/windcode/extensions/mcp/adapter.py`、`src/windcode/policy/engine.py`、`src/windcode/policy/models.py`、`tests/integration/test_extensions_mcp_policy.py`
**依赖：** T23

**步骤：**
1. 未知 MCP Tool 至少标记外部副作用并默认进入审批流程。
2. 忽略 Server 的只读注解对授权级别的影响，仅用于用户提示。
3. Server/Tool 固定策略只能收紧或缓存当前权限模式已经允许的决定。
4. MCP 调用复用 Scheduler、审批、取消、审计和运行预算。
5. 添加注解欺骗、固定策略、父权限上限、拒绝和取消测试。

**验证：** 运行 `uv run pytest -q tests/integration/test_extensions_mcp_policy.py`，期望全部通过。

## T25：实现 MCP Resource、Prompt 与 Instructions 工具

**文件：** `src/windcode/extensions/mcp/tools.py`、`src/windcode/tools/builtins.py`、`src/windcode/runtime/prompts.py`、`tests/unit/extensions/test_mcp_tools.py`
**依赖：** T22、T23、T24

**步骤：**
1. 实现列出和读取 Resource、列出和获取 Prompt 的受控工具入口。
2. 所有内容保留 Server、URI/名称和快照来源，并应用大小限制与 artifact 外置。
3. Instructions 使用独立来源标签进入有界上下文，不拼接成无来源核心指令。
4. 未信任、禁用或未激活 Server 的内容不得注入模型。
5. 添加参数、来源、超限、错误和提示注入边界测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_mcp_tools.py`，期望全部通过。

## T26：实现大型 MCP 工具集渐进暴露

**文件：** `src/windcode/extensions/mcp/tools.py`、`src/windcode/tools/registry.py`、`src/windcode/runtime/loop.py`、`src/windcode/runtime/prompts.py`、`tests/unit/extensions/test_mcp_exposure.py`
**依赖：** T18、T22、T23

**步骤：**
1. 低于阈值时直接注册完整 Schema；超过阈值时只注入精简目录与搜索工具。
2. 实现搜索和 `select:<stable-id>` 激活，完整 Schema 从下一模型步可见。
3. 动态暴露集合只属于当前运行，不修改全局 Registry 或快照。
4. 完整目录不写入历史消息，重复模型步不会线性累积上下文。
5. 添加阈值边界、搜索排序、运行隔离和上下文体积测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_mcp_exposure.py tests/unit/test_context_truncation.py`，期望全部通过。

## T27：拆分 Scheduler 安全扩展点

**文件：** `src/windcode/runtime/scheduler.py`、`src/windcode/domain/tools.py`、`tests/unit/test_scheduler.py`
**依赖：** T18

**步骤：**
1. 将现有执行拦截拆为参数校验后、策略前、执行前和执行后阶段。
2. 策略前结果只能拒绝或增加 effects、路径和网络限制。
3. 确保拒绝发生在审批、副作用和 `ToolStarted` 持久化之前。
4. 执行后观察不能替换 ToolResult 或改写历史事件。
5. 保持未注册拦截器时现有工具调度顺序和错误语义不变。

**验证：** 运行 `uv run pytest -q tests/unit/test_scheduler.py tests/integration/test_runtime_approval.py`，期望全部通过。

## T28：定义 Hook 配置、事件和匹配模型

**文件：** `src/windcode/extensions/hooks/__init__.py`、`src/windcode/extensions/hooks/models.py`、`src/windcode/extensions/hooks/loader.py`、`tests/unit/extensions/test_hook_models.py`
**依赖：** T3、T4、T6

**步骤：**
1. 定义稳定高层事件、版本化冻结上下文、匹配器、动作和约束结果。
2. 匹配条件只允许事件、稳定工具 ID、状态和有限字段，不提供表达式执行。
3. 校验 notify、prompt、command、reject 和 tighten 的事件适用范围。
4. 对来源、优先级、超时、输出上限和必需性进行严格解析。
5. 添加非法动作组合、未知事件、重复 ID 和确定性顺序测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_hook_models.py`，期望全部通过。

## T29：实现 Hook 调度与故障隔离

**文件：** `src/windcode/extensions/hooks/dispatcher.py`、`tests/unit/extensions/test_hook_dispatcher.py`
**依赖：** T9、T27、T28

**步骤：**
1. 同步预 Hook 按稳定优先级串行执行，通知型后置 Hook 使用有界并发。
2. 为每个 Hook 设置超时、取消、单次执行计数和首版不重试语义。
3. 安全判定型 Hook 异常 fail closed；观察型异常只记录并隔离。
4. 实现同源递归保护和运行级收敛，结束前等待或取消后置任务。
5. 添加顺序、超时、异常、取消、递归和可选/必需故障测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_hook_dispatcher.py`，期望全部通过。

## T30：实现 Hook 通知与带来源提示动作

**文件：** `src/windcode/extensions/hooks/executor.py`、`src/windcode/domain/messages.py`、`tests/unit/extensions/test_hook_actions.py`
**依赖：** T10、T28、T29

**步骤：**
1. notify 动作产生结构化事件，不直接写终端或依赖 TUI。
2. prompt 动作产生有界 `SourcedContextMessage` 并排入下一模型步。
3. 后置提示不能修改现有消息、工具结果或历史事件。
4. Hook 参数和输出应用既有脱敏、截断和来源标签。
5. 添加来源持久化、下一步可见、历史不可变和超大输出测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_hook_actions.py tests/unit/test_prompts.py`，期望全部通过。

## T31：实现 Hook 命令、拒绝与收紧动作

**文件：** `src/windcode/extensions/hooks/executor.py`、`src/windcode/runtime/scheduler.py`、`tests/integration/test_extensions_hook_policy.py`
**依赖：** T24、T27、T29、T30

**步骤：**
1. Hook command 创建带来源的内部 ScheduledCall，复用 Shell、Policy、Sandbox 和审批。
2. 禁止 command 触发同源递归 Hook，并限制超时、输出和调用次数。
3. reject 在副作用前终止原调用，tighten 只增加限制且不可被后续 Hook 放宽。
4. Hook 失败不得将已拒绝操作变为允许或自动无限重试。
5. 添加命令审批、沙箱、拒绝无副作用、多个收紧合并和 fail-closed 测试。

**验证：** 运行 `uv run pytest -q tests/integration/test_extensions_hook_policy.py`，期望全部通过。

## T32：接入会话、用户、运行与工具 Hook 生命周期

**文件：** `src/windcode/runtime/loop.py`、`src/windcode/runtime/scheduler.py`、`src/windcode/sdk.py`、`tests/integration/test_extensions_hooks.py`
**依赖：** T27、T29、T30、T31

**步骤：**
1. 在 session start/end、user submit、run start/end/error 发出稳定 Hook 上下文。
2. 在 tool before policy、permission request 和 tool after 接入安全顺序。
3. 所有 Hook 事件带 session、run、source 和 correlation ID。
4. 运行异常与取消路径同样执行适用的结束 Hook 并完成资源收敛。
5. 添加成功、拒绝、审批、异常和取消生命周期顺序测试。

**验证：** 运行 `uv run pytest -q tests/integration/test_extensions_hooks.py -k 'session or run or tool or permission'`，期望全部通过。

## T33：接入压缩与子智能体 Hook 生命周期

**文件：** `src/windcode/context/compactor.py`、`src/windcode/runtime/subagents/`、`src/windcode/sdk.py`、`tests/integration/test_extensions_hooks.py`
**依赖：** T32

**步骤：**
1. 在 compact before/after 发出有界摘要元数据，不暴露完整隐藏历史。
2. 在 subagent start/end 发出父子关联、角色、状态和用量摘要。
3. 子运行使用父运行固定快照，不能因服务重载看到新扩展。
4. Hook 不得借子智能体生命周期启动新的委派或提升子权限。
5. 添加压缩成功/失败、子任务完成/取消和父子来源测试。

**验证：** 运行 `uv run pytest -q tests/integration/test_extensions_hooks.py -k 'compact or subagent' tests/integration/test_subagent_runtime.py`，期望全部通过。

## T34：组装运行级扩展资源与关闭树

**文件：** `src/windcode/sdk.py`、`src/windcode/runtime/loop.py`、`src/windcode/extensions/runtime.py`、`tests/integration/test_extensions_runtime.py`
**依赖：** T13、T16、T21、T26、T32、T33

**步骤：**
1. `start_run()` 原子取得当前快照并创建运行专属 Tool View、Skill Loader、MCP Runtime 和 Hook Dispatcher。
2. 使用 `AsyncExitStack` 或等价所有权树管理连接、子进程、任务和审批等待。
3. 正常完成、失败、取消和 SDK 关闭均按依赖逆序关闭运行级资源。
4. 禁用与重载只影响之后的新运行，旧运行继续使用原 generation。
5. 添加两个并发运行、重载隔离和四类关闭路径的泄漏测试。

**验证：** 运行 `uv run pytest -q tests/integration/test_extensions_runtime.py`，期望全部通过且无遗留任务、连接或进程。

## T35：强化扩展输出脱敏与诊断

**文件：** `src/windcode/observability/redaction.py`、`src/windcode/sessions/artifacts.py`、`src/windcode/extensions/models.py`、`tests/unit/extensions/test_security.py`
**依赖：** T5、T10、T20、T23、T30

**步骤：**
1. 支持运行时注册环境密钥、静态头值和已识别敏感值，并在关闭后清理。
2. 扩展诊断包含阶段、来源、类别和可操作建议，但不泄露密钥或完整外部输出。
3. MCP、Hook、Skill 和清单内容统一应用模型视图、终端视图和 artifact 边界。
4. 外部错误中的敏感路径和请求头使用来源标签或摘要替代。
5. 添加配置、事件、轨迹、异常、模型上下文和终端输出的全链路泄漏测试。

**验证：** 运行 `uv run pytest -q tests/unit/extensions/test_security.py tests/unit/test_redaction.py tests/unit/test_trace.py`，期望全部通过。

## T36：完成 TUI 扩展管理体验

**文件：** `src/windcode/tui/commands.py`、`src/windcode/tui/app.py`、`src/windcode/tui/widgets/extensions.py`、`src/windcode/tui/widgets/__init__.py`、`src/windcode/tui/styles.tcss`、`tests/integration/tui/test_extensions.py`
**依赖：** T13、T17

**步骤：**
1. 增加 `/extensions` 管理视图，展示稳定排序的状态、来源、信任、权限和诊断。
2. 列出与检查使用只读流程；安装、信任、启禁和重载显示明确确认及结果。
3. 插件声明式命令进入统一命令目录，并显示插件来源且正确处理冲突。
4. 所有操作调用 SDK 服务，不维护独立 TUI 状态或扫描逻辑。
5. 在 40x24、80x24、120x36 下验证长 ID、诊断和权限文本不重叠。

**验证：** 运行 `uv run pytest -q tests/integration/tui/test_extensions.py tests/integration/tui/test_commands.py`，期望全部通过。

## T37：建立确定性 MCP 契约测试夹具

**文件：** `tests/contract/mcp/conftest.py`、`tests/contract/mcp/stdio_server.py`、`tests/contract/mcp/http_server.py`、`tests/contract/mcp/test_lifecycle.py`
**依赖：** T19、T20、T22

**步骤：**
1. 提供无需模型凭据和外部网络的本地 stdio 与 HTTP Server。
2. 可配置 Tools、Resources、Prompts、Instructions、认证、延迟、崩溃和超大输出。
3. 为每个 Server 暴露进程、连接和任务计数，测试结束断言全部归零。
4. 固定协议响应和时间控制，避免依赖真实时钟竞态。
5. 将真实远程 MCP 场景单独标记为显式 smoke test。

**验证：** 运行 `uv run pytest -q tests/contract/mcp`，期望本地契约全部通过且远程测试默认跳过。

## T38：验证信任、插件和快照集成

**文件：** `tests/integration/test_extensions_discovery.py`、`tests/integration/test_extensions_runtime.py`
**依赖：** T12、T13、T34、T35

**步骤：**
1. 构造用户、项目、运行级独立资源和多组件插件。
2. 验证未信任项目资源可检查但不激活、不执行和不注入。
3. 信任并显式重载后仅新运行可使用，旧运行保持旧 generation。
4. 验证同层冲突、插件组件冲突、可选失败和必需失败语义。
5. 重复改变文件遍历顺序，断言能力 ID、覆盖和诊断完全稳定。

**验证：** 运行 `uv run pytest -q tests/integration/test_extensions_discovery.py tests/integration/test_extensions_runtime.py -k 'trust or snapshot or conflict or required'`，期望全部通过。

## T39：验证 MCP 全链路集成

**文件：** `tests/integration/test_extensions_mcp.py`
**依赖：** T24、T25、T26、T34、T35、T37

**步骤：**
1. 让模拟模型按需激活 stdio 和 HTTP Server 的四类能力。
2. 验证 Tool 复用参数校验、审批、调度、取消、预算、事件和 artifact。
3. 验证大工具集搜索激活后仅目标 Schema 从下一模型步可见。
4. 覆盖传输失败、一次重连、可选故障隔离和必需启动失败。
5. 运行结束与取消后断言无连接、进程、任务和审批等待泄漏。

**验证：** 运行 `uv run pytest -q tests/integration/test_extensions_mcp.py`，期望全部通过。

## T40：验证 Hooks 全链路集成

**文件：** `tests/integration/test_extensions_hooks.py`、`tests/integration/test_extensions_hook_policy.py`
**依赖：** T31、T32、T33、T34、T35

**步骤：**
1. 模拟完整运行并验证全部稳定 Hook 事件的顺序和关联字段。
2. 验证预 Hook 拒绝及收紧发生在副作用前，后置 Hook 不能替换结果。
3. 验证 command 动作经过审批、沙箱、超时、输出限制和审计。
4. 覆盖递归、异常、取消、可选隔离和必需 fail-closed。
5. 验证提示动作仅在下一模型步以带来源内容出现。

**验证：** 运行 `uv run pytest -q tests/integration/test_extensions_hooks.py tests/integration/test_extensions_hook_policy.py`，期望全部通过。

## T41：验证 CLI、TUI 与 SDK 状态一致性

**文件：** `tests/integration/test_extensions_interfaces.py`、`tests/integration/tui/test_extensions.py`
**依赖：** T14、T36、T38

**步骤：**
1. 三个入口依次对同一本地插件执行列出、检查、安装、启用、禁用和重载。
2. 比较能力状态、来源、权限、诊断、generation 和事件语义。
3. 确认任一入口完成状态变化后，其他入口不需要维护或同步私有状态。
4. 覆盖失败安装、未信任项目、必需扩展失败和并发重载。
5. 验证插件命令在三个入口共享同一声明式路由结果。

**验证：** 运行 `uv run pytest -q tests/integration/test_extensions_interfaces.py tests/integration/tui/test_extensions.py`，期望全部通过。

## T42：实现完整本地插件端到端场景

**文件：** `tests/e2e/test_extension_runtime.py`、`tests/fixtures/extensions/complete_plugin/`
**依赖：** T17、T39、T40、T41

**步骤：**
1. 构造包含 Agent Skill、stdio MCP、工具前后 Hooks 和声明式命令的本地插件。
2. 从 TUI 完成检查、安装、信任、启用与重载，再由命令触发 Skill。
3. 按需调用 MCP Tool，观察预 Hook 拒绝和后置通知，检查完整审计时间线。
4. 通过 SDK 禁用并重载，证明旧运行保持可用而新运行不再暴露插件。
5. 断言安装阶段无执行、所有内容带来源且退出后无资源泄漏。

**验证：** 运行 `uv run pytest -q tests/e2e/test_extension_runtime.py`，期望完整场景全部通过。

## T43：验证无扩展与旧数据兼容

**文件：** `tests/integration/test_extensions_disabled.py`、既有单智能体、多智能体、会话、配置、TUI 与 SDK 测试
**依赖：** T34、T41、T42

**步骤：**
1. 在未配置和显式禁用两种状态运行启动探针。
2. 断言不扫描用户兼容目录、不启动进程、不建立网络连接且只使用空快照。
3. 读取旧配置和旧会话，确认不迁移、不重写且未知新事件可忽略。
4. 运行现有单智能体、多智能体、权限、沙箱、恢复、TUI 和 SDK 场景。
5. 比较关键启动调用计数，确认扩展关闭路径没有隐藏后台工作。

**验证：** 运行 `uv run pytest -q tests/integration/test_extensions_disabled.py tests/e2e/test_coding_task.py tests/e2e/test_multi_agent_coding_task.py`，期望全部通过。

## T44：执行完整质量验证

**文件：** 本轮全部文件
**依赖：** T1-T43

**步骤：**
1. 运行格式检查和 Lint，修复本轮引入的问题。
2. 运行严格类型检查，确保扩展边界无未知类型泄漏。
3. 清除模型密钥并运行完整测试，确认默认套件无外部网络依赖。
4. 构建源码包和 Wheel，检查扩展包及测试夹具未误入发布物。
5. 运行 `git diff --check`，并把实际验收证据记录到 `checklist.md`。

**验证：** 依次运行 `uv run ruff format --check .`、`uv run ruff check .`、`uv run pyright`、`uv run pytest -q`、`uv build`、`git diff --check`，期望全部退出码为 0。

## 执行顺序

```text
T1 ── T2 ───────────────────────────────────────────────┐
 │                                                      │
 └── T18 ── T19 ── T20 ── T21 ── T22 ── T23 ── T24 ─┐│
                                                     ├┼─ T25 ── T26 ───────┐
T3 ── T4 ── T5 ──┬─ T6 ──┬─ T8 ── T9 ── T11 ── T13 ┘│                   │
                  │       │              └─ T12        │                   │
                  └─ T7 ──┘                            │                   │
T10 ───────────────────────────────────────────────────┘                   │
                                                                            │
T9 + T15 ── T16 ── T17                                                     │
T18 ── T27 ── T28 ── T29 ── T30 ── T31 ── T32 ── T33                      │
                                                                            │
T13 + T16 + T21 + T26 + T32 + T33 ── T34 ── T35                           │
T13 ── T14                                                                  │
T13 + T17 ── T36                                                            │
T19 + T20 + T22 ── T37                                                      │
T12 + T13 + T34 + T35 ── T38                                                │
T24 + T25 + T26 + T34 + T35 + T37 ── T39                                   │
T31 + T32 + T33 + T34 + T35 ── T40                                         │
T14 + T36 + T38 ── T41                                                      │
T17 + T39 + T40 + T41 ── T42 ── T43 ── T44                                │
```
