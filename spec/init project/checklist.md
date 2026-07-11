# Windcode 纵向 MVP Checklist

> 每一项均通过运行代码或观察外部行为验证；实现细节重构后，只要行为不变，检查项仍应成立。

## 实现完整性

- [x] AC1：同一脚本化编码任务通过 SDK 与 Textual TUI 运行时，文本、工具、审批、用量和终态事件语义一致（验证：运行 `uv run pytest tests/e2e/test_coding_task.py -q`，观察两个入口的规范化事件序列断言通过）。
- [x] AC2：Anthropic Messages、OpenAI Responses 和 OpenAI-compatible Chat Completions 均能把文本、推理状态、多个工具调用、用量及错误归一化为统一事件（验证：运行 `uv run pytest tests/contract -q`，观察三组共享契约全部通过）。
- [x] AC3：Agent 能完成读取、修改、运行测试和报告结果的循环，并在模型步骤预算耗尽时停止（验证：运行 `uv run pytest tests/integration/test_agent_loop.py tests/e2e/test_coding_task.py -q`，观察完成路径和预算终止路径通过）。
- [x] AC4：取消模型流或长时间 Shell 后，模型请求和子进程停止，不再执行后续工具，会话终态为 cancelled（验证：运行 `uv run pytest tests/integration/test_agent_loop.py tests/unit/tools/test_shell.py -q -k cancel`，观察取消传播和进程回收断言通过）。
- [x] AC5：`read_file`、`write_file`、`edit_file`、`apply_patch`、`glob`、`grep`、`shell`、`ask_user` 均覆盖成功、参数错误和执行失败，错误可反馈给 Agent（验证：运行 `uv run pytest tests/unit/tools -q`，观察八种工具场景全部通过）。
- [x] AC6：连续只读工具并发执行，写入、Shell 和交互工具按调用顺序独占执行，结果顺序稳定（验证：运行 `uv run pytest tests/unit/test_scheduler.py -q`，观察并发时间与顺序断言通过）。
- [x] AC7：`plan`、`default`、`accept_edits`、`full_access` 四种模式行为符合设计，审批拒绝不产生副作用并形成工具拒绝结果（验证：运行 `uv run pytest tests/unit/test_policy_engine.py tests/integration/test_runtime_approval.py -q`）。
- [x] AC8：bubblewrap 可用时阻止越界写入和未授权网络；不可用时显示降级且 Shell 被提升为审批（验证：运行 `uv run pytest tests/integration/test_security.py -q`，观察隔离与缺失夹具均通过）。
- [x] AC9：重启可恢复消息、工具状态和上下文；历史回退后可创建不覆盖原链的新分支（验证：运行 `uv run pytest tests/unit/test_session_tree.py tests/integration/test_session_crash_recovery.py tests/integration/test_sdk.py -q`）。
- [x] AC10：副作用工具执行前后模拟崩溃时，未闭合请求恢复为 interrupted，不标记成功且不自动重放（验证：运行 `uv run pytest tests/integration/test_session_crash_recovery.py -q -k interrupted`）。
- [x] AC11：从仓库根到当前目录按就近优先加载指令，同层按 `AGENTS.md`、`WINDCODE.md`、`CLAUDE.md`、`HERMES.md` 选择（验证：运行 `uv run pytest tests/unit/test_instructions.py -q`）。
- [x] AC12：上下文超过阈值后先裁剪低价值内容再生成结构化检查点，任务可继续且原始事件不丢失；摘要失败时保留原视图（验证：运行 `uv run pytest tests/unit/test_context_truncation.py tests/unit/test_compactor.py tests/integration/test_runtime_compaction.py -q`）。
- [x] AC13：可重试错误最多重试两次，耗尽后严格按配置链切换模型，并产生包含原模型、原因和目标模型的事件（验证：运行 `uv run pytest tests/integration/test_model_fallback.py -q`）。
- [x] AC14：配置按默认、用户、项目、显式覆盖顺序生效；缺失密钥、未知字段和未知协议产生可操作错误，日志无密钥（验证：运行 `uv run pytest tests/unit/test_config_models.py tests/unit/test_config_loader.py tests/unit/test_redaction.py -q`）。
- [x] AC15：外部工具和 transport 无需修改核心即可注册，并复用参数校验、权限、事件和取消机制（验证：运行 `uv run pytest tests/integration/test_sdk.py -q -k custom`）。
- [x] AC16：完整运行产生本地可关联事件时间线，记录中无密钥或敏感字段，且默认不发出遥测请求（验证：运行 `uv run pytest tests/unit/test_trace.py tests/integration/test_sdk.py -q -k 'trace or telemetry or redact'`）。
- [x] AC17：完成报告列出真实文件变化、命令、退出码和测试证据；测试失败或未验证时不显示完全成功（验证：运行 `uv run pytest tests/unit/test_run_report.py tests/e2e/test_coding_task.py -q`）。
- [x] AC18：Linux 环境下格式、lint、严格类型检查和完整离线测试通过；无模型凭据时测试仍可完整执行（验证：清除模型凭据后运行 `uv run ruff format --check . && uv run ruff check . && uv run pyright && uv run pytest -q`，观察退出码均为 0）。

## 公共 SDK 与事件协议

- [x] `windcode.sdk` 和 `windcode.types` 可独立导入，深层内部模块不需要用于正常集成（验证：运行 `uv run python -c "from windcode import Windcode; from windcode.types import RunRequest, AgentEvent, Tool"`）。
- [x] `RunHandle` 支持异步迭代、审批/提问回复、取消和幂等取得结果（验证：运行 `uv run pytest tests/integration/test_run_handle.py -q`）。
- [x] 公共事件均包含事件、会话、运行、时间和轮次关联字段，并可 JSON 往返（验证：运行 `uv run pytest tests/unit/test_events.py -q`）。
- [x] 事件必须先写入 SessionStore 和 TraceStore，再对消费者可见；从序号恢复时无遗漏或重复（验证：运行 `uv run pytest tests/unit/test_event_bus.py -q`）。
- [x] 跨模型回退保留可迁移文本、工具调用与结果，但不泄露或错误迁移私有推理状态（验证：运行 `uv run pytest tests/integration/test_model_fallback.py -q -k opaque`）。

## 工具、安全与文件一致性

- [x] 文件工具拒绝工作区逃逸和符号链接越界（验证：运行 `uv run pytest tests/unit/test_filesystem_helpers.py tests/integration/test_security.py -q -k 'escape or symlink'`）。
- [x] 写入、精确编辑和补丁在内容摘要过期时不覆盖外部修改（验证：运行 `uv run pytest tests/unit/tools/test_write_file.py tests/unit/tools/test_edit_file.py tests/unit/tools/test_apply_patch.py -q -k 'stale or conflict'`）。
- [x] 多文件补丁预检失败时不留下部分修改（验证：运行 `uv run pytest tests/unit/tools/test_apply_patch.py -q -k rollback`）。
- [x] Shell 输出有界、stdout/stderr 可观察、超时后进程组被回收（验证：运行 `uv run pytest tests/unit/tools/test_shell.py -q`）。
- [x] `full_access` 与禁用沙箱只能由用户或启动配置显式选择，模型工具调用无法切换（验证：运行 `uv run pytest tests/integration/test_security.py tests/unit/test_cli.py -q -k explicit`）。

## 会话、上下文与可靠性

- [x] JSONL 尾部存在不完整记录时，仅该尾行被忽略，之前记录仍可恢复（验证：运行 `uv run pytest tests/unit/test_session_recovery.py -q -k tail`）。
- [x] 大型工具结果完整写入 artifact，模型视图仅保留摘要和可解析引用（验证：运行 `uv run pytest tests/unit/test_artifacts.py tests/unit/test_context_truncation.py -q`）。
- [x] 上下文压缩保留系统指令、最近八轮及未闭合工具调用（验证：运行 `uv run pytest tests/unit/test_context_truncation.py -q -k preserve`）。
- [x] 达到模型步骤、工具调用或运行时间任一预算时产生明确终态且不启动新工具（验证：运行 `uv run pytest tests/unit/test_run_control.py tests/integration/test_agent_loop.py -q -k budget`）。
- [x] 所有 transport、子进程、事件任务和文件句柄在正常结束、失败与取消后关闭（验证：运行 `uv run pytest tests/integration/test_sdk.py tests/unit/tools/test_shell.py -q -W error`，观察无资源泄漏警告）。

## TUI 与 CLI

- [x] `python -m windcode --help` 和 `windcode --help` 均展示工作区、配置、模型、恢复、权限和沙箱参数（验证：运行两个帮助命令并观察退出码为 0）。
- [x] TUI 在流式输出和工具运行期间仍可接收取消与交互输入（验证：运行 `uv run pytest tests/integration/tui/test_app.py tests/integration/tui/test_stream_widgets.py -q`）。
- [x] 工具块可展示参数摘要、进度、耗时、退出码和折叠输出（验证：运行 `uv run pytest tests/integration/tui/test_stream_widgets.py -q`，观察组件快照通过）。
- [x] 内联审批和一至三个问题支持键盘操作、提交与取消（验证：运行 `uv run pytest tests/integration/tui/test_interactions.py -q`）。
- [x] 状态栏展示模型、权限、沙箱、上下文和用量，会话选择器支持恢复、回退与分支（验证：运行 `uv run pytest tests/integration/tui/test_sessions.py -q`）。
- [x] 八个 slash 命令均通过 SDK 完成，不直接绕过运行时或存储（验证：运行 `uv run pytest tests/integration/tui/test_commands.py -q`）。
- [x] 窄终端尺寸下主输入、审批和状态信息仍可访问，无未处理布局异常（验证：运行 `uv run pytest tests/integration/tui -q -k narrow`）。

## 编译、测试与发布

- [x] 依赖可从锁文件复现安装（验证：在干净虚拟环境运行 `uv sync --frozen --all-groups`）。
- [x] Ruff 格式检查通过（验证：运行 `uv run ruff format --check .`）。
- [x] Ruff lint 通过且无警告（验证：运行 `uv run ruff check .`）。
- [x] Pyright strict 通过（验证：运行 `uv run pyright`）。
- [x] 完整离线测试通过（验证：运行 `uv run pytest -q`）。
- [x] 真实 provider 冒烟测试无凭据时明确跳过，有显式开关和凭据时只执行固定短响应（验证：运行 `uv run pytest tests/smoke -q`）。
- [x] 源码分发包含 Apache-2.0 许可证，包元数据与项目名均为 Windcode（验证：运行 `uv build`，检查 wheel/sdist 元数据和 `LICENSE`）。
- [x] README 的安装、配置和 SDK 示例与实际公共接口一致（验证：执行 README 中的导入示例和 `uv run python -m windcode --help`）。

## 端到端场景

- [x] 场景 1：用户在默认模式提交修复任务 → Agent 读取文件 → 请求修改审批 → 修改代码 → 请求执行测试审批 → 测试通过 → 最终报告列出文件和验证证据（验证：运行 `uv run pytest tests/e2e/test_coding_task.py -q -k success`）。
- [x] 场景 2：用户拒绝写入审批 → 文件保持不变 → Agent 收到拒绝结果 → 会话可继续且审计记录完整（验证：运行 `uv run pytest tests/e2e/test_coding_task.py -q -k denied`）。
- [x] 场景 3：主模型连续瞬时失败 → 两次有限重试 → 显式切换回退模型 → 继续原任务并展示回退事件（验证：运行 `uv run pytest tests/e2e/test_coding_task.py -q -k fallback`）。
- [x] 场景 4：长任务触发上下文压缩 → 检查点保留目标、文件、进度和证据 → Agent 完成后续修改（验证：运行 `uv run pytest tests/e2e/test_coding_task.py -q -k compact`）。
- [x] 场景 5：Shell 执行中进程被取消或异常退出 → 子进程停止 → 会话恢复为 interrupted/cancelled → 操作不自动重放（验证：运行 `uv run pytest tests/e2e/test_coding_task.py tests/integration/test_session_crash_recovery.py -q -k 'cancel or interrupted'`）。
