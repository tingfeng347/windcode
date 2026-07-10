# Windcode 纵向 MVP Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `pyproject.toml` | 项目元数据、依赖、入口和质量工具配置 |
| 新建 | `LICENSE` | Apache-2.0 许可证 |
| 修改 | `README.md` | 安装、配置、TUI 与 SDK 快速开始 |
| 删除 | `main.py` | 移除占位入口，改用包入口 |
| 新建 | `src/windcode/__init__.py`、`__main__.py`、`cli.py`、`sdk.py`、`types.py` | 包入口与稳定公共 API |
| 新建 | `src/windcode/domain/*.py` | 统一消息、事件、错误和工具契约 |
| 新建 | `src/windcode/config/*.py` | TOML 配置模型与分层加载 |
| 新建 | `src/windcode/instructions/*.py` | 项目指令发现与优先级处理 |
| 新建 | `src/windcode/providers/*.py` | 三协议传输、错误归一化和回退注册 |
| 新建 | `src/windcode/tools/*.py` | 工具注册表与八种内置工具 |
| 新建 | `src/windcode/policy/*.py` | 权限模式、风险判定与审批 |
| 新建 | `src/windcode/sandbox/*.py` | bubblewrap 检测与命令包装 |
| 新建 | `src/windcode/sessions/*.py` | 追加式事件树、恢复、分支和 artifacts |
| 新建 | `src/windcode/context/*.py` | token 估算、裁剪与检查点压缩 |
| 新建 | `src/windcode/observability/*.py` | 本地脱敏 JSONL 轨迹 |
| 新建 | `src/windcode/runtime/*.py` | Run 控制、调度、Agent Loop 和完成报告 |
| 新建 | `src/windcode/tui/*.py`、`styles.tcss` | Textual 聊天工作台及命令 |
| 新建 | `tests/unit/*.py` | 纯模块单元测试 |
| 新建 | `tests/contract/*.py` | 三模型协议模拟服务契约测试 |
| 新建 | `tests/integration/*.py` | SDK、运行时、安全、会话和 TUI 集成测试 |
| 新建 | `tests/e2e/*.py` | 完整模拟编码任务 |

## T1：建立 src-layout 与构建元数据

**文件：** `pyproject.toml`、`src/windcode/__init__.py`
**依赖：** 无
**步骤：**
1. 配置 Python 3.12、hatchling、`windcode` 命令入口和 src-layout。
2. 添加 Textual、Pydantic、Anthropic/OpenAI aiohttp extras、aiohttp、platformdirs 及开发依赖。
3. 配置 Ruff、Pyright strict、Pytest 和 asyncio 测试模式。

**验证：** 运行 `uv sync --all-groups`；期望依赖解析成功并生成锁文件。

## T2：添加许可证与包入口

**文件：** `LICENSE`、`src/windcode/__main__.py`、`main.py`
**依赖：** T1
**步骤：**
1. 添加标准 Apache-2.0 许可证文本。
2. 让模块入口调用 CLI 主函数。
3. 删除根目录占位 `main.py`。

**验证：** 运行 `uv run python -m windcode --help`；期望显示帮助且无导入错误。

## T3：定义统一消息类型

**文件：** `src/windcode/domain/messages.py`
**依赖：** T1
**步骤：**
1. 定义角色、文本、推理摘要、工具调用、工具结果和附件引用不可变类型。
2. 定义 `Message` 及 opaque provider metadata。

**验证：** 运行 `uv run pyright src/windcode/domain/messages.py`；期望 0 errors。

## T4：定义统一模型事件与请求

**文件：** `src/windcode/domain/models.py`
**依赖：** T3
**步骤：**
1. 定义模型请求、工具 schema、用量和完成原因。
2. 定义文本增量、推理状态、工具调用增量、用量和完成模型事件联合。

**验证：** 运行 `uv run pyright src/windcode/domain/models.py`；期望 0 errors。

## T5：定义 Agent 公共事件

**文件：** `src/windcode/domain/events.py`
**依赖：** T3、T4
**步骤：**
1. 定义计划中全部公共事件及共同 envelope 字段。
2. 提供事件到 JSON 安全字典的序列化函数。

**验证：** 运行 `uv run pytest tests/unit/test_events.py -q`；期望事件往返测试通过。

## T6：定义错误分类

**文件：** `src/windcode/domain/errors.py`
**依赖：** T4
**步骤：**
1. 定义认证、限流、网络、服务端、上下文、请求、内容限制与取消类别。
2. 定义错误是否可重试及是否允许模型回退的属性。

**验证：** 运行 `uv run pytest tests/unit/test_errors.py -q`；期望分类矩阵通过。

## T7：定义工具公共契约

**文件：** `src/windcode/domain/tools.py`
**依赖：** T3
**步骤：**
1. 定义 `ToolEffect`、`ToolContext`、`ToolResult` 和 `Tool` Protocol。
2. 定义工具调用参数验证错误的结构化结果。

**验证：** 运行 `uv run pyright src/windcode/domain/tools.py`；期望 0 errors。

## T8：汇总稳定公共类型

**文件：** `src/windcode/types.py`、`src/windcode/domain/__init__.py`
**依赖：** T3-T7
**步骤：**
1. 只重导出计划承诺的消息、事件、工具、请求与响应类型。
2. 设置显式 `__all__`，避免暴露内部实现。

**验证：** 运行 `uv run python -c "from windcode.types import RunRequest, AgentEvent, Tool"`；期望退出码 0。

## T9：建立严格配置模型

**文件：** `src/windcode/config/models.py`
**依赖：** T6
**步骤：**
1. 定义 provider、预算、权限、沙箱、上下文和应用配置。
2. 禁止未知字段和明文 API Key，校验回退链引用。

**验证：** 运行 `uv run pytest tests/unit/test_config_models.py -q`；期望合法/非法样例通过。

## T10：实现 TOML 分层加载

**文件：** `src/windcode/config/loader.py`、`src/windcode/config/__init__.py`
**依赖：** T9
**步骤：**
1. 按默认、用户、项目、显式覆盖顺序深度合并配置。
2. 解析 XDG 路径并输出带来源位置的错误。

**验证：** 运行 `uv run pytest tests/unit/test_config_loader.py -q`；期望优先级和错误来源测试通过。

## T11：实现项目指令发现

**文件：** `src/windcode/instructions/loader.py`、`src/windcode/instructions/__init__.py`
**依赖：** T3
**步骤：**
1. 定位 Git 根或工作区根并逐层扫描目录。
2. 每层按 `AGENTS.md`、`WINDCODE.md`、`CLAUDE.md`、`HERMES.md` 选择首个文件。
3. 返回按根到当前目录排序且带来源的指令块。

**验证：** 运行 `uv run pytest tests/unit/test_instructions.py -q`；期望层级与同层优先级通过。

## T12：定义会话记录模型

**文件：** `src/windcode/sessions/models.py`
**依赖：** T5
**步骤：**
1. 定义 session metadata、事件记录、父记录引用和 artifact 引用。
2. 定义 schema 版本与恢复状态。

**验证：** 运行 `uv run pytest tests/unit/test_session_models.py -q`；期望序列化往返通过。

## T13：实现追加式事件写入

**文件：** `src/windcode/sessions/store.py`
**依赖：** T12
**步骤：**
1. 创建会话目录并以单调序号追加 JSONL。
2. 在副作用边界执行 flush/fsync，并原子更新 meta。

**验证：** 运行 `uv run pytest tests/unit/test_session_store_append.py -q`；期望顺序、父节点和刷盘测试通过。

## T14：实现会话恢复与尾行修复

**文件：** `src/windcode/sessions/store.py`
**依赖：** T13
**步骤：**
1. 加载有效记录并忽略唯一损坏的末行。
2. 将无结果的副作用请求恢复为 interrupted 记录。

**验证：** 运行 `uv run pytest tests/unit/test_session_recovery.py -q`；期望损坏尾行与中断工具测试通过。

## T15：实现回退与分支

**文件：** `src/windcode/sessions/tree.py`、`src/windcode/sessions/__init__.py`
**依赖：** T14
**步骤：**
1. 从任意记录计算有效祖先链。
2. 以历史记录为父节点创建新分支，不修改原链。

**验证：** 运行 `uv run pytest tests/unit/test_session_tree.py -q`；期望回退、分叉和原历史不变测试通过。

## T16：实现大型输出 artifact 存储

**文件：** `src/windcode/sessions/artifacts.py`
**依赖：** T13
**步骤：**
1. 将超阈值工具输出以内容摘要命名并原子写入 artifacts。
2. 返回短摘要、内容长度、摘要值和相对引用。

**验证：** 运行 `uv run pytest tests/unit/test_artifacts.py -q`；期望去重与内容恢复通过。

## T17：实现敏感信息脱敏

**文件：** `src/windcode/observability/redaction.py`
**依赖：** T9
**步骤：**
1. 脱敏认证头、密钥环境变量值和敏感字段。
2. 递归处理嵌套映射和序列且不修改输入。

**验证：** 运行 `uv run pytest tests/unit/test_redaction.py -q`；期望密钥不出现在输出。

## T18：实现本地 JSONL 轨迹

**文件：** `src/windcode/observability/trace.py`、`src/windcode/observability/__init__.py`
**依赖：** T5、T17
**步骤：**
1. 按 run 写入脱敏事件、耗时和错误类别。
2. 使用 XDG state 路径并允许配置关闭详细参数。

**验证：** 运行 `uv run pytest tests/unit/test_trace.py -q`；期望关联字段完整且无敏感内容。

## T19：定义模型传输基类与错误映射工具

**文件：** `src/windcode/providers/base.py`、`src/windcode/providers/errors.py`
**依赖：** T4、T6、T9
**步骤：**
1. 定义 `ModelTransport` Protocol 和共享连接生命周期。
2. 实现 HTTP 状态、SDK 异常到统一错误的映射。

**验证：** 运行 `uv run pytest tests/unit/test_provider_errors.py -q`；期望异常映射矩阵通过。

## T20：实现 Anthropic Messages 适配器

**文件：** `src/windcode/providers/anthropic.py`
**依赖：** T19
**步骤：**
1. 使用官方异步 SDK aiohttp 客户端且禁用 SDK 重试。
2. 双向转换消息、工具 schema、流式文本、工具调用、推理状态和用量。

**验证：** 运行 `uv run pytest tests/contract/test_anthropic.py -q`；期望模拟流和错误场景通过。

## T21：实现 OpenAI Responses 适配器

**文件：** `src/windcode/providers/openai_responses.py`
**依赖：** T19
**步骤：**
1. 使用官方异步 SDK aiohttp 客户端且禁用 SDK 重试。
2. 转换 Responses 输入项、增量事件、工具调用、推理摘要和用量。

**验证：** 运行 `uv run pytest tests/contract/test_openai_responses.py -q`；期望模拟流和错误场景通过。

## T22：实现 OpenAI-compatible 适配器

**文件：** `src/windcode/providers/openai_compat.py`
**依赖：** T19
**步骤：**
1. 使用 aiohttp 发起 Chat Completions SSE 请求。
2. 解析文本、分片工具参数、完成原因、用量及错误响应。

**验证：** 运行 `uv run pytest tests/contract/test_openai_compat.py -q`；期望分片、断流和错误场景通过。

## T23：实现 transport 注册表与模型链

**文件：** `src/windcode/providers/registry.py`、`src/windcode/providers/__init__.py`
**依赖：** T20-T22
**步骤：**
1. 根据配置实例化内置或自定义 transport。
2. 解析主模型及有序回退链并检测循环。

**验证：** 运行 `uv run pytest tests/unit/test_provider_registry.py -q`；期望注册、覆盖和回退顺序通过。

## T24：实现工具注册表

**文件：** `src/windcode/tools/registry.py`、`src/windcode/tools/__init__.py`
**依赖：** T7
**步骤：**
1. 注册工具并拒绝重复名称。
2. 从 Pydantic 输入模型生成统一工具 schema。

**验证：** 运行 `uv run pytest tests/unit/test_tool_registry.py -q`；期望注册和 schema 测试通过。

## T25：实现安全路径解析与文件摘要

**文件：** `src/windcode/tools/filesystem.py`
**依赖：** T7
**步骤：**
1. 规范化相对/绝对路径并标记工作区内外及符号链接越界。
2. 提供文件内容摘要和原子替换辅助函数。

**验证：** 运行 `uv run pytest tests/unit/test_filesystem_helpers.py -q`；期望路径与 symlink 场景通过。

## T26：实现 read_file

**文件：** `src/windcode/tools/read_file.py`
**依赖：** T24、T25
**步骤：**
1. 支持带行号的分段文本读取和大小限制。
2. 返回内容摘要供后续并发修改检查。

**验证：** 运行 `uv run pytest tests/unit/tools/test_read_file.py -q`；期望范围、编码和大文件测试通过。

## T27：实现 write_file

**文件：** `src/windcode/tools/write_file.py`
**依赖：** T25、T26
**步骤：**
1. 校验可选旧摘要并原子写入目标。
2. 返回创建/修改状态和统一 diff 摘要。

**验证：** 运行 `uv run pytest tests/unit/tools/test_write_file.py -q`；期望写入和陈旧摘要拒绝测试通过。

## T28：实现 edit_file

**文件：** `src/windcode/tools/edit_file.py`
**依赖：** T25
**步骤：**
1. 要求旧文本唯一匹配并执行精确替换。
2. 多匹配、零匹配或摘要过期时不写入并返回错误。

**验证：** 运行 `uv run pytest tests/unit/tools/test_edit_file.py -q`；期望唯一性与无副作用失败通过。

## T29：实现 apply_patch

**文件：** `src/windcode/tools/apply_patch.py`
**依赖：** T25
**步骤：**
1. 解析受限 unified diff，禁止路径穿越和二进制补丁。
2. 预检全部 hunk 后再原子应用，失败时不留下部分修改。

**验证：** 运行 `uv run pytest tests/unit/tools/test_apply_patch.py -q`；期望多文件、冲突和回滚测试通过。

## T30：实现 glob 与 grep

**文件：** `src/windcode/tools/glob.py`、`src/windcode/tools/grep.py`
**依赖：** T24、T25
**步骤：**
1. 实现限定工作区、排序稳定、结果有上限的文件匹配。
2. 实现文本搜索、上下文行、二进制跳过和结果截断。

**验证：** 运行 `uv run pytest tests/unit/tools/test_search.py -q`；期望排序、上限和越界测试通过。

## T31：定义权限请求与决策类型

**文件：** `src/windcode/policy/models.py`
**依赖：** T7
**步骤：**
1. 定义四种权限模式、风险等级、策略动作和审批选择。
2. 定义可展示且不含敏感值的审批请求。

**验证：** 运行 `uv run pytest tests/unit/test_policy_models.py -q`；期望序列化和选择校验通过。

## T32：实现策略引擎

**文件：** `src/windcode/policy/engine.py`、`src/windcode/policy/__init__.py`
**依赖：** T25、T31
**步骤：**
1. 按权限模式和 `ToolEffect` 返回 allow、deny 或 ask。
2. 将越界、网络和危险 Shell 提升风险，支持单次/会话批准。

**验证：** 运行 `uv run pytest tests/unit/test_policy_engine.py -q`；期望四模式决策矩阵通过。

## T33：实现 bubblewrap 沙箱配置

**文件：** `src/windcode/sandbox/bwrap.py`、`src/windcode/sandbox/__init__.py`
**依赖：** T9、T25
**步骤：**
1. 检测 bubblewrap 可用性并生成只读系统、可写工作区、独立 `/tmp` 的参数。
2. 默认隔离网络，仅在该次调用获批时共享网络。

**验证：** 运行 `uv run pytest tests/unit/test_bwrap.py -q`；期望参数快照和缺失降级测试通过。

## T34：实现 shell 工具与取消

**文件：** `src/windcode/tools/shell.py`
**依赖：** T24、T32、T33
**步骤：**
1. 通过异步子进程流式读取 stdout/stderr，执行超时和输出上限。
2. 取消时终止进程组并等待回收，返回退出码、耗时和截断状态。

**验证：** 运行 `uv run pytest tests/unit/tools/test_shell.py -q`；期望输出、超时、取消和沙箱包装通过。

## T35：实现 ask_user 工具

**文件：** `src/windcode/tools/ask_user.py`
**依赖：** T24
**步骤：**
1. 校验一至三个问题及互斥选项。
2. 通过运行控制通道等待回复或取消，不直接依赖 TUI。

**验证：** 运行 `uv run pytest tests/unit/tools/test_ask_user.py -q`；期望回复、校验和取消通过。

## T36：注册八种内置工具

**文件：** `src/windcode/tools/builtins.py`
**依赖：** T26-T30、T34、T35
**步骤：**
1. 以稳定名称和 effect 集合注册八种工具。
2. 确认生成的工具 schema 无重复且可 JSON 序列化。

**验证：** 运行 `uv run pytest tests/unit/tools/test_builtins.py -q`；期望名称、effect 和 schema 快照通过。

## T37：实现工具调度器

**文件：** `src/windcode/runtime/scheduler.py`
**依赖：** T24、T32
**步骤：**
1. 将连续只读调用并发执行，写入/执行/交互调用独占执行。
2. 无论完成顺序如何，按原调用顺序返回结果。

**验证：** 运行 `uv run pytest tests/unit/test_scheduler.py -q`；期望并行时序和结果顺序通过。

## T38：实现 token 估算

**文件：** `src/windcode/context/estimator.py`
**依赖：** T3、T4
**步骤：**
1. 使用实际 usage 锚点加保守增量估算当前上下文。
2. 计算压缩阈值、输出保留和剩余预算。

**验证：** 运行 `uv run pytest tests/unit/test_token_estimator.py -q`；期望阈值边界通过。

## T39：实现确定性上下文裁剪

**文件：** `src/windcode/context/truncation.py`
**依赖：** T16、T38
**步骤：**
1. 移除旧媒体并将大型工具输出转存 artifact。
2. 保留最近八轮、系统指令和未闭合工具调用。

**验证：** 运行 `uv run pytest tests/unit/test_context_truncation.py -q`；期望保留集合和原历史不变通过。

## T40：实现模型检查点压缩

**文件：** `src/windcode/context/compactor.py`、`src/windcode/context/__init__.py`
**依赖：** T19、T39
**步骤：**
1. 构建固定八节检查点提示并调用当前 transport。
2. 校验摘要内容；失败时返回原视图而不丢弃历史。

**验证：** 运行 `uv run pytest tests/unit/test_compactor.py -q`；期望成功、失败保留和摘要结构通过。

## T41：实现系统提示与环境上下文

**文件：** `src/windcode/runtime/prompts.py`
**依赖：** T11、T36
**步骤：**
1. 组合核心行为、工具说明、权限模式、工作区和项目指令。
2. 明确要求基于实际验证报告结果，不虚报成功。

**验证：** 运行 `uv run pytest tests/unit/test_prompts.py -q`；期望顺序、来源和模式提示通过。

## T42：实现运行预算与取消控制

**文件：** `src/windcode/runtime/control.py`
**依赖：** T5、T31
**步骤：**
1. 跟踪模型步骤、工具调用、墙钟时间和取消状态。
2. 管理审批/提问 Future，并在取消时解除全部等待。

**验证：** 运行 `uv run pytest tests/unit/test_run_control.py -q`；期望预算和取消传播通过。

## T43：实现事件先落盘再分发

**文件：** `src/windcode/runtime/event_bus.py`
**依赖：** T13、T18、T42
**步骤：**
1. 事件先写 SessionStore 与 TraceStore，再放入消费者队列。
2. 支持从指定序号回放后继续消费实时事件。

**验证：** 运行 `uv run pytest tests/unit/test_event_bus.py -q`；期望持久化先于可见事件和回放无重复。

## T44：实现 Agent Loop 基础路径

**文件：** `src/windcode/runtime/loop.py`
**依赖：** T23、T37、T40-T43
**步骤：**
1. 实现消息构造、模型流收集、工具调用执行和结果反馈循环。
2. 在无工具调用时完成，在预算/取消/不可恢复错误时产生对应终态。

**验证：** 运行 `uv run pytest tests/integration/test_agent_loop.py -q`；期望完成、预算和错误路径通过。

## T45：集成审批与用户提问

**文件：** `src/windcode/runtime/loop.py`
**依赖：** T32、T35、T44
**步骤：**
1. 对 ask 决策发出 `ApprovalRequested` 并暂停对应工具。
2. 将批准、拒绝或取消转换为工具结果并继续或终止循环。

**验证：** 运行 `uv run pytest tests/integration/test_runtime_approval.py -q`；期望批准、拒绝、会话批准和取消通过。

## T46：集成重试与显式模型回退

**文件：** `src/windcode/runtime/retry.py`、`src/windcode/runtime/loop.py`
**依赖：** T6、T23、T44
**步骤：**
1. 对可重试错误执行最多两次带抖动退避并发出事件。
2. 耗尽后按配置切换模型，剥离不可迁移 opaque 推理块并发出回退事件。

**验证：** 运行 `uv run pytest tests/integration/test_model_fallback.py -q`；期望重试次数、事件和链顺序通过。

## T47：集成自动压缩

**文件：** `src/windcode/runtime/loop.py`
**依赖：** T38-T40、T44
**步骤：**
1. 每次采样前检查阈值并按需生成检查点视图。
2. 发出压缩事件并将摘要记录到会话，失败时保留原视图。

**验证：** 运行 `uv run pytest tests/integration/test_runtime_compaction.py -q`；期望触发、继续和失败保留通过。

## T48：实现完成与验证报告

**文件：** `src/windcode/runtime/report.py`
**依赖：** T27-T30、T34、T44
**步骤：**
1. 汇总工具记录中的文件变更、命令、退出码和测试证据。
2. 测试失败或无验证时禁止生成完全成功状态。

**验证：** 运行 `uv run pytest tests/unit/test_run_report.py -q`；期望成功、未验证和失败报告通过。

## T49：实现 RunHandle

**文件：** `src/windcode/sdk.py`
**依赖：** T42-T48
**步骤：**
1. 实现异步迭代、`respond()`、`cancel()` 和 `result()`。
2. 确保提前退出迭代时仍可显式取消并关闭资源。

**验证：** 运行 `uv run pytest tests/integration/test_run_handle.py -q`；期望迭代、响应、取消和结果幂等通过。

## T50：实现 Windcode 装配与扩展注册

**文件：** `src/windcode/sdk.py`、`src/windcode/__init__.py`
**依赖：** T10、T23、T36、T49
**步骤：**
1. 实现异步上下文管理器，装配并关闭 transports、stores 和运行任务。
2. 实现 `register_tool()` 与 `register_transport()`，复用内置验证与策略路径。

**验证：** 运行 `uv run pytest tests/integration/test_sdk.py -q`；期望公共 API 与自定义扩展场景通过。

## T51：实现 CLI 参数解析

**文件：** `src/windcode/cli.py`
**依赖：** T10、T50
**步骤：**
1. 支持工作目录、配置文件、模型、恢复会话、权限模式和沙箱开关。
2. 校验显式 full access/禁用沙箱并将配置错误输出到 stderr。

**验证：** 运行 `uv run pytest tests/unit/test_cli.py -q`；期望帮助、参数和错误码通过。

## T52：建立 Textual 应用骨架

**文件：** `src/windcode/tui/app.py`、`src/windcode/tui/__init__.py`、`src/windcode/tui/styles.tcss`
**依赖：** T49、T51
**步骤：**
1. 建立消息区、输入区、状态栏和 SDK 生命周期。
2. 实现首次 Ctrl+C 取消运行、空闲或再次 Ctrl+C 退出。

**验证：** 运行 `uv run pytest tests/integration/tui/test_app.py -q`；期望挂载、提交和取消测试通过。

## T53：实现消息与工具块组件

**文件：** `src/windcode/tui/widgets/messages.py`、`src/windcode/tui/widgets/tools.py`
**依赖：** T5、T52
**步骤：**
1. 增量合并文本并展示推理状态、用量和终态。
2. 工具块显示名称、参数摘要、进度、耗时、退出码和可折叠输出。

**验证：** 运行 `uv run pytest tests/integration/tui/test_stream_widgets.py -q`；期望事件渲染快照通过。

## T54：实现内联审批与提问组件

**文件：** `src/windcode/tui/widgets/approval.py`、`src/windcode/tui/widgets/question.py`
**依赖：** T31、T35、T52
**步骤：**
1. 渲染风险、操作摘要和允许的审批选项并回复 RunHandle。
2. 渲染一至三个问题，支持键盘导航、提交和取消。

**验证：** 运行 `uv run pytest tests/integration/tui/test_interactions.py -q`；期望审批与提问流程通过。

## T55：实现状态栏与会话选择器

**文件：** `src/windcode/tui/widgets/status.py`、`src/windcode/tui/widgets/sessions.py`
**依赖：** T15、T49、T52
**步骤：**
1. 展示模型、权限、沙箱、上下文占用和累计用量。
2. 列出会话并支持恢复、选择回退节点和创建分支。

**验证：** 运行 `uv run pytest tests/integration/tui/test_sessions.py -q`；期望状态更新和分支选择通过。

## T56：实现 slash 命令

**文件：** `src/windcode/tui/commands.py`
**依赖：** T50、T55
**步骤：**
1. 解析并分派 `/new`、`/resume`、`/rewind`、`/mode`、`/model`、`/compact`、`/status`、`/quit`。
2. 对运行中不安全的切换给出明确拒绝，不绕过 SDK。

**验证：** 运行 `uv run pytest tests/integration/tui/test_commands.py -q`；期望各命令及非法状态测试通过。

## T57：连接 CLI 与 TUI

**文件：** `src/windcode/cli.py`、`src/windcode/__main__.py`
**依赖：** T52-T56
**步骤：**
1. 从 CLI 配置创建 Windcode SDK 并启动 Textual 应用。
2. 保证退出时取消运行并关闭 provider/session/trace 资源。

**验证：** 运行 `uv run python -m windcode --help` 和 `uv run pytest tests/integration/test_cli_tui.py -q`；期望入口与关闭流程通过。

## T58：补齐三协议共享契约测试夹具

**文件：** `tests/contract/conftest.py`、`tests/contract/test_shared_contract.py`
**依赖：** T20-T23
**步骤：**
1. 建立本地 aiohttp 模拟服务与统一期望事件序列。
2. 对三适配器复用文本、工具、用量、错误和取消契约。

**验证：** 运行 `uv run pytest tests/contract -q`；期望三协议全部通过且无外网请求。

## T59：补齐安全集成测试

**文件：** `tests/integration/test_security.py`
**依赖：** T32-T34、T45
**步骤：**
1. 覆盖四模式、symlink 越界、网络隔离和审批拒绝。
2. 在 bubblewrap 缺失夹具下验证警告与审批升级。

**验证：** 运行 `uv run pytest tests/integration/test_security.py -q`；期望全部安全场景通过。

## T60：补齐会话崩溃恢复测试

**文件：** `tests/integration/test_session_crash_recovery.py`
**依赖：** T14-T16、T43
**步骤：**
1. 模拟副作用工具开始前后进程中断。
2. 验证恢复状态、无自动重放、回退和分支保持原历史。

**验证：** 运行 `uv run pytest tests/integration/test_session_crash_recovery.py -q`；期望全部恢复场景通过。

## T61：实现端到端模拟编码任务

**文件：** `tests/e2e/test_coding_task.py`
**依赖：** T44-T57
**步骤：**
1. 建立带失败测试的临时示例项目和脚本化模型 transport。
2. 驱动读取、审批、修改、运行测试和完成报告。
3. 分别通过 SDK 和 TUI 验证语义一致的事件与文件结果。

**验证：** 运行 `uv run pytest tests/e2e/test_coding_task.py -q`；期望两个入口都完成且报告包含真实测试证据。

## T62：实现可选真实模型冒烟测试

**文件：** `tests/smoke/test_real_providers.py`
**依赖：** T20-T23
**步骤：**
1. 仅在对应显式环境开关和凭据同时存在时运行。
2. 对每个已配置协议请求固定短响应，不调用工具或修改文件。

**验证：** 运行 `uv run pytest tests/smoke -q`；无凭据时全部 skip，有凭据时返回预期短响应。

## T63：编写用户与 SDK 文档

**文件：** `README.md`、`.windcode/config.toml.example`
**依赖：** T50-T57
**步骤：**
1. 记录安装、三协议配置、环境变量、权限模式、沙箱降级和 TUI 命令。
2. 提供 SDK 异步迭代、审批、自定义工具和自定义 transport 示例。

**验证：** 运行 README 中的导入示例和 `uv run python -m windcode --help`；期望命令与公共导入有效。

## T64：执行全量质量门禁

**文件：** 全部实现与测试文件
**依赖：** T1-T63
**步骤：**
1. 运行格式、lint、严格类型检查和完整离线测试。
2. 修复所有错误、警告、事件循环泄漏和未关闭资源。

**验证：** 运行 `uv run ruff format --check . && uv run ruff check . && uv run pyright && uv run pytest -q`；期望全部退出码为 0。

## 执行顺序

```text
T1-T2
  → T3-T8
  → T9-T18
  → T19-T23
  → T24-T37
  → T38-T43
  → T44-T50
  → T51-T57
  → T58-T63
  → T64
```
