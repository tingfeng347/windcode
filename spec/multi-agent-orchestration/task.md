# Windcode 临时多智能体并行编排 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/windcode/config/models.py` | 委派模式、容量和预算配置 |
| 新建 | `src/windcode/domain/subagents.py` | 子任务、角色、状态、记录和结果类型 |
| 修改 | `src/windcode/domain/events.py` | 子智能体事件、来源审批和序列化 |
| 修改 | `src/windcode/tools/registry.py` | 为每次根运行克隆工具注册表 |
| 修改 | `src/windcode/tools/builtins.py` | 组装根专属编排工具 |
| 新建 | `src/windcode/tools/subagents/__init__.py` | 编排工具导出与注册辅助函数 |
| 新建 | `src/windcode/tools/subagents/spawn.py` | 批量创建工具及参数模型 |
| 新建 | `src/windcode/tools/subagents/list.py` | 状态查询工具 |
| 新建 | `src/windcode/tools/subagents/cancel.py` | 单任务取消工具 |
| 新建 | `src/windcode/tools/subagents/integrate.py` | 提交集成与父级验证工具 |
| 新建 | `src/windcode/runtime/subagents/__init__.py` | 编排运行时公共导出 |
| 新建 | `src/windcode/runtime/subagents/roles.py` | 三种内建角色与工具交集 |
| 新建 | `src/windcode/runtime/subagents/budgets.py` | 独立预算与父级聚合预算 |
| 新建 | `src/windcode/runtime/subagents/approvals.py` | 子审批到父会话的精确路由 |
| 新建 | `src/windcode/runtime/subagents/verification.py` | 父工作区验证命令执行适配 |
| 新建 | `src/windcode/runtime/subagents/factory.py` | 独立子运行时构造 |
| 新建 | `src/windcode/runtime/subagents/coordinator.py` | 队列、状态机、执行、取消、恢复和集成 |
| 修改 | `src/windcode/runtime/prompts.py` | 显式/主动委派模式指令 |
| 新建 | `src/windcode/worktrees/__init__.py` | Worktree 类型和管理器导出 |
| 新建 | `src/windcode/worktrees/models.py` | Git 基线、租约、检查、集成和清理结果 |
| 新建 | `src/windcode/worktrees/git.py` | 无交互 Git 命令执行与错误分类 |
| 新建 | `src/windcode/worktrees/manager.py` | Worktree 创建、检查、集成、恢复和清理 |
| 修改 | `src/windcode/sdk.py` | 根协调器组装、生命周期和 SDK 控制面 |
| 修改 | `src/windcode/types.py` | 公开子智能体类型和事件 |
| 修改 | `src/windcode/tui/app.py` | 子事件、审批、查询与集成交互 |
| 修改 | `src/windcode/tui/commands.py` | `/agents` 命令定义与解析 |
| 修改 | `src/windcode/tui/styles.tcss` | 子任务组和状态样式 |
| 修改 | `src/windcode/tui/widgets/__init__.py` | 子任务组件导出 |
| 修改 | `src/windcode/tui/widgets/approval.py` | 显示子任务审批来源 |
| 新建 | `src/windcode/tui/widgets/subagents.py` | 子任务组、行和展开详情组件 |
| 修改 | `tests/unit/test_config_models.py` | 委派配置边界测试 |
| 修改 | `tests/unit/test_prompts.py` | 委派模式提示测试 |
| 修改 | `tests/unit/test_tool_registry.py` | 注册表克隆与隔离测试 |
| 修改 | `tests/unit/tools/test_builtins.py` | 根/子工具可见性测试 |
| 新建 | `tests/unit/test_subagent_models.py` | 任务校验、状态和序列化测试 |
| 新建 | `tests/unit/test_subagent_roles.py` | 角色与工具交集测试 |
| 新建 | `tests/unit/test_subagent_budgets.py` | 独立及聚合预算测试 |
| 新建 | `tests/unit/test_subagent_events.py` | 新事件往返与来源字段测试 |
| 新建 | `tests/unit/test_subagent_coordinator.py` | FIFO、状态机、容量和结果顺序测试 |
| 新建 | `tests/unit/test_subagent_verification.py` | 父级验证结果与取消测试 |
| 新建 | `tests/unit/test_worktree_manager.py` | Git 基线、隔离、集成、冲突和清理测试 |
| 新建 | `tests/integration/test_subagent_runtime.py` | 独立上下文和并发运行时测试 |
| 新建 | `tests/integration/test_subagent_approval.py` | 多子任务审批路由测试 |
| 新建 | `tests/integration/test_subagent_cancellation.py` | 单任务及父任务取消测试 |
| 新建 | `tests/integration/test_subagent_integration.py` | 写任务提交、集成和父级验证测试 |
| 新建 | `tests/integration/test_subagent_recovery.py` | 异常恢复与遗留 Worktree 测试 |
| 修改 | `tests/integration/test_sdk.py` | SDK 子任务查询、取消和集成契约 |
| 新建 | `tests/integration/tui/test_subagents.py` | TUI 状态、审批和 `/agents` 测试 |
| 新建 | `tests/e2e/test_multi_agent_coding_task.py` | 完整并行编码任务场景 |

## T1：增加委派配置模型

**文件：** `src/windcode/config/models.py`、`tests/unit/test_config_models.py`  
**依赖：** 无

**步骤：**
1. 定义 `DelegationMode` 和 `SubagentConfig`，加入批准的默认容量与预算。
2. 将不可配置的硬上限定义为模块常量，并校验配置值和聚合预算关系。
3. 把 `subagents` 配置加入 `AppConfig`，保持未知字段拒绝行为。
4. 添加默认值、合法覆盖、超硬上限和无效聚合预算测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_config_models.py`，期望全部通过。

## T2：定义子智能体领域模型

**文件：** `src/windcode/domain/subagents.py`、`tests/unit/test_subagent_models.py`  
**依赖：** T1

**步骤：**
1. 定义角色、任务类型、状态、验证结果、任务规格、记录和结果类型。
2. 实现任务名、必填内容、角色任务类型和工具收紧校验。
3. 定义并检查合法状态转换，拒绝终态回退。
4. 添加构造、校验、稳定排序和状态转换测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_subagent_models.py`，期望全部通过。

## T3：增加子智能体生命周期事件

**文件：** `src/windcode/domain/events.py`、`tests/unit/test_subagent_events.py`  
**依赖：** T2

**步骤：**
1. 定义排队、启动、进度、阻塞、完成、失败、取消、集成、冲突和清理事件。
2. 为事件加入父运行、子任务、序号、角色和脱敏摘要字段。
3. 为 `ApprovalRequested` 增加可选子任务来源字段，默认值保持旧调用兼容。
4. 更新事件联合类型和序列化/反序列化分支。
5. 添加所有新事件往返及旧审批载荷兼容测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_events.py tests/unit/test_subagent_events.py`，期望全部通过。

## T4：实现工具注册表克隆

**文件：** `src/windcode/tools/registry.py`、`tests/unit/test_tool_registry.py`  
**依赖：** 无

**步骤：**
1. 增加返回独立注册表的 `clone()`，保留已注册工具对象和顺序。
2. 确认在克隆表替换工具不会修改源注册表。
3. 添加克隆隔离、模式顺序和自定义工具保留测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_tool_registry.py`，期望全部通过。

## T5：实现内建角色策略

**文件：** `src/windcode/runtime/subagents/roles.py`、`tests/unit/test_subagent_roles.py`  
**依赖：** T2

**步骤：**
1. 定义 `RolePolicy` 和三个内建角色目录。
2. 实现角色工具、任务工具和父工具的交集计算。
3. 对读取型任务移除写入、越界和网络能力，并保留受限只读 Shell。
4. 添加角色任务类型、工具收紧、未知工具和禁止提权测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_subagent_roles.py`，期望全部通过。

## T6：实现聚合预算

**文件：** `src/windcode/runtime/subagents/budgets.py`、`tests/unit/test_subagent_budgets.py`  
**依赖：** T1

**步骤：**
1. 定义 `AggregateBudget` 和聚合用量快照。
2. 为模型步骤、工具调用和运行时检查增加并发安全计数。
3. 返回可区分独立预算和聚合预算的错误类别。
4. 添加并发消费、边界耗尽、不返还消费和用量快照测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_subagent_budgets.py`，期望全部通过。

## T7：定义 Worktree 领域结果

**文件：** `src/windcode/worktrees/models.py`、`src/windcode/worktrees/__init__.py`  
**依赖：** T2

**步骤：**
1. 定义 `GitBaseline`、`WorktreeLease`、`WorktreeResult`、`IntegrationResult` 和 `CleanupResult`。
2. 定义 Git 错误类别和可操作错误类型。
3. 从包入口导出稳定类型。

**验证：** 运行 `uv run python -m py_compile src/windcode/worktrees/models.py src/windcode/worktrees/__init__.py`，期望退出码为 0。

## T8：封装无交互 Git 执行

**文件：** `src/windcode/worktrees/git.py`、`tests/unit/test_worktree_manager.py`  
**依赖：** T7

**步骤：**
1. 实现参数数组形式的异步 Git 调用，禁用终端提示和凭据交互。
2. 捕获退出码、标准输出、标准错误和超时。
3. 对仓库缺失、命令失败、超时和取消进行结构化分类。
4. 添加成功、失败和超时的最小测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_worktree_manager.py -k git_runner`，期望全部通过。

## T9：实现父仓库基线校验

**文件：** `src/windcode/worktrees/manager.py`、`tests/unit/test_worktree_manager.py`  
**依赖：** T8

**步骤：**
1. 检测仓库根、当前分支、HEAD 和 Worktree 支持。
2. 同时检查已跟踪、未跟踪、暂存和冲突修改。
3. 干净仓库返回不可变 `GitBaseline`，脏仓库返回具体阻塞原因。
4. 添加干净、脏、非 Git 和 detached HEAD 测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_worktree_manager.py -k baseline`，期望全部通过。

## T10：实现 Worktree 创建

**文件：** `src/windcode/worktrees/manager.py`、`tests/unit/test_worktree_manager.py`  
**依赖：** T9

**步骤：**
1. 生成包含父运行和任务标识的安全分支名及目录名。
2. 在仓库级异步锁内从基线提交创建分支和 Worktree。
3. 创建后复核路径、分支和 HEAD，并返回 `WorktreeLease`。
4. 添加两个并行租约互异和父工作区不变测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_worktree_manager.py -k create`，期望全部通过。

## T11：实现 Worktree 检查与清理

**文件：** `src/windcode/worktrees/manager.py`、`tests/unit/test_worktree_manager.py`  
**依赖：** T10

**步骤：**
1. 检查工作区是否干净、是否产生新提交及其变更文件和 Diff 统计。
2. 只删除已集成且干净的租约。
3. 对未提交、失败、未集成或路径不匹配的租约返回保留结果。
4. 添加干净删除和各类保留测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_worktree_manager.py -k 'inspect or cleanup'`，期望全部通过。

## T12：实现提交集成与冲突中止

**文件：** `src/windcode/worktrees/manager.py`、`tests/unit/test_worktree_manager.py`  
**依赖：** T11

**步骤：**
1. 在集成前重新检查父工作区干净状态并记录父 HEAD。
2. 串行执行 `cherry-pick`，成功时返回前后提交。
3. 冲突时收集冲突文件、执行中止并确认父 HEAD 恢复。
4. 中止或恢复失败时保留现场并返回独立错误类别。
5. 添加成功集成、内容冲突和恢复校验测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_worktree_manager.py -k integrate`，期望全部通过。

## T13：实现 Worktree 恢复校验

**文件：** `src/windcode/worktrees/manager.py`、`tests/unit/test_worktree_manager.py`  
**依赖：** T11

**步骤：**
1. 根据持久化记录重新验证路径、Git 归属、分支和基线。
2. 对缺失或已被用户改变的资源返回保留诊断，不创建新资源。
3. 添加有效遗留、缺失目录和路径错配测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_worktree_manager.py -k recover`，期望全部通过。

## T14：实现审批路由

**文件：** `src/windcode/runtime/subagents/approvals.py`、`tests/integration/test_subagent_approval.py`  
**依赖：** T2、T3

**步骤：**
1. 为每个子请求生成父请求 ID，并保存一对一映射。
2. 发布带子任务身份、工具和风险的父审批事件。
3. 将响应精确投递回对应子控制器并清理映射。
4. 取消子任务时取消其待处理审批，不影响兄弟请求。
5. 添加两个并发审批交叉响应和单独取消测试。

**验证：** 运行 `uv run pytest -q tests/integration/test_subagent_approval.py`，期望全部通过。

## T15：实现父级验证执行器

**文件：** `src/windcode/runtime/subagents/verification.py`、`tests/unit/test_subagent_verification.py`  
**依赖：** T2

**步骤：**
1. 复用 Shell 沙箱、超时、取消和输出截断执行验证命令。
2. 按输入顺序生成结构化 `VerificationResult`。
3. 首个失败命令后停止后续命令，并保留实际输出摘要。
4. 添加成功、失败、超时和取消测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_subagent_verification.py`，期望全部通过。

## T16：构造独立子运行时

**文件：** `src/windcode/runtime/subagents/factory.py`、`src/windcode/runtime/subagents/__init__.py`  
**依赖：** T4、T5、T6、T14

**步骤：**
1. 为每个子任务创建独立会话、事件总线、控制器、策略和工具注册表。
2. 复用模型传输连接，但不共享会话相关可变对象。
3. 按角色、任务和父工具集计算有效工具，并移除编排工具和用户问答工具。
4. 重新加载子工作区项目指令并构造自包含首条输入。
5. 将子问答请求转换为 `blocked` 信号。

**验证：** 运行 `uv run python -m py_compile src/windcode/runtime/subagents/factory.py`，期望退出码为 0。

## T17：实现协调器状态与批量预检

**文件：** `src/windcode/runtime/subagents/coordinator.py`、`tests/unit/test_subagent_coordinator.py`  
**依赖：** T2、T6、T9、T16

**步骤：**
1. 实现任务表、稳定编号、状态转换和列表快照。
2. 在创建任何记录前完成整批任务、重复名称、角色、容量和 Git 预检。
3. 超过并发数时按输入顺序进入 FIFO，超过总量时整批拒绝。
4. 状态变化先追加父记录，再发布父事件。
5. 添加原子预检、8 个总量、4 路并发和稳定编号测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_subagent_coordinator.py -k 'validate or capacity or queue'`，期望全部通过。

## T18：实现协调器异步执行

**文件：** `src/windcode/runtime/subagents/coordinator.py`、`tests/unit/test_subagent_coordinator.py`  
**依赖：** T10、T16、T17

**步骤：**
1. 从 FIFO 获取并发槽并启动子运行时任务。
2. 为写任务创建租约，为读任务绑定只读父工作区。
3. 把子进度转换为父级脱敏事件，完整事件留在子会话。
4. 捕获完成、阻塞、失败和预算错误，释放槽后启动队首任务。
5. 批量结果按任务序号返回，不按完成顺序重排。

**验证：** 运行 `uv run pytest -q tests/unit/test_subagent_coordinator.py -k 'execute or result_order'`，期望全部通过。

## T19：实现协调器取消与关闭

**文件：** `src/windcode/runtime/subagents/coordinator.py`、`tests/integration/test_subagent_cancellation.py`  
**依赖：** T14、T18

**步骤：**
1. 排队任务取消后直接进入终态且永不启动。
2. 运行任务取消传播到模型、审批等待和工具执行。
3. 根关闭停止接收新任务，并等待全部取消和保守清理完成。
4. 保证取消单个任务不影响兄弟任务。
5. 添加排队取消、运行取消、兄弟隔离和父取消测试。

**验证：** 运行 `uv run pytest -q tests/integration/test_subagent_cancellation.py`，期望全部通过。

## T20：实现协调器集成流程

**文件：** `src/windcode/runtime/subagents/coordinator.py`、`tests/integration/test_subagent_integration.py`  
**依赖：** T12、T15、T18

**步骤：**
1. 只允许具有干净提交的已完成写任务进入集成。
2. 调用 Worktree 管理器执行 `cherry-pick`，再运行已审批的父级验证命令。
3. 成功时标记集成并清理；验证失败时标记 `integration_failed`，保留已集成提交、失败证据和子 Worktree。
4. 冲突时发布冲突事件并保留分支和 Worktree。
5. 添加成功、验证失败和冲突流程测试。

**验证：** 运行 `uv run pytest -q tests/integration/test_subagent_integration.py`，期望全部通过。

## T21：实现协调器异常恢复

**文件：** `src/windcode/runtime/subagents/coordinator.py`、`tests/integration/test_subagent_recovery.py`  
**依赖：** T13、T19

**步骤：**
1. 从父会话记录重建任务快照和结果索引。
2. 将遗留 `queued`、`running` 状态恢复为中断后的取消状态。
3. 校验并报告遗留分支和 Worktree，不启动模型或副作用。
4. 添加异常退出、遗留成果、缺失资源和幂等恢复测试。

**验证：** 运行 `uv run pytest -q tests/integration/test_subagent_recovery.py`，期望全部通过。

## T22：实现创建与查询工具

**文件：** `src/windcode/tools/subagents/spawn.py`、`src/windcode/tools/subagents/list.py`、`src/windcode/tools/subagents/__init__.py`  
**依赖：** T17、T18

**步骤：**
1. 定义严格的批量任务参数模型并转换为 `SubagentTaskSpec`。
2. `spawn_subagents` 调用协调器并立即返回任务标识和初始状态。
3. `list_subagents` 返回按任务序号排序的结构化快照。
4. 统一参数、容量和执行错误类别。

**验证：** 运行 `uv run python -m py_compile src/windcode/tools/subagents/spawn.py src/windcode/tools/subagents/list.py`，期望退出码为 0。

## T23：实现取消与集成工具

**文件：** `src/windcode/tools/subagents/cancel.py`、`src/windcode/tools/subagents/integrate.py`、`src/windcode/tools/subagents/__init__.py`  
**依赖：** T19、T20

**步骤：**
1. `cancel_subagent` 校验标识并返回取消后的状态。
2. `integrate_subagent` 接收提交目标和父级验证命令。
3. 为集成工具声明工作区写入和进程副作用，审批摘要展示提交、分支和命令。
4. 将冲突、验证失败和不可集成状态映射为结构化结果。

**验证：** 运行 `uv run python -m py_compile src/windcode/tools/subagents/cancel.py src/windcode/tools/subagents/integrate.py`，期望退出码为 0。

## T24：注入根专属编排工具

**文件：** `src/windcode/tools/builtins.py`、`src/windcode/tools/registry.py`  
**依赖：** T4、T22、T23

**步骤：**
1. 提供接收协调器并注册四个工具的辅助函数。
2. 根运行从共享注册表克隆后注入编排工具。
3. 子运行时使用不含编排工具的独立注册表。
4. 添加根/子工具可见性和并发根运行隔离测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_tool_registry.py tests/unit/tools/test_builtins.py`，期望全部通过。

## T25：增加委派模式系统指令

**文件：** `src/windcode/runtime/prompts.py`、`tests/unit/test_prompts.py`  
**依赖：** T1

**步骤：**
1. 显式模式注入“仅在用户明确请求时委派”的开发约束。
2. 主动模式注入允许自主判断但要求可见和有界的约束。
3. 子运行时提示中加入扁平拓扑、自包含结果和禁止直接提问规则。
4. 添加两种根模式和子提示内容测试。

**验证：** 运行 `uv run pytest -q tests/unit/test_prompts.py`，期望全部通过。

## T26：组装 SDK 根协调器

**文件：** `src/windcode/sdk.py`、`tests/integration/test_sdk.py`  
**依赖：** T16、T21、T24、T25

**步骤：**
1. 每次 `start_run` 克隆工具注册表并创建该运行专属协调器。
2. 把协调器、Worktree 管理器、审批路由和子运行时工厂组装到同一生命周期。
3. 根任务结束、取消或 SDK 关闭时先关闭协调器。
4. 恢复旧会话时调用协调器恢复，但不续跑任务。
5. 添加两个并发根运行不串用协调器的测试。

**验证：** 运行 `uv run pytest -q tests/integration/test_sdk.py`，期望全部通过。

## T27：扩展 RunHandle 子任务控制面

**文件：** `src/windcode/sdk.py`、`tests/integration/test_sdk.py`  
**依赖：** T26

**步骤：**
1. 为 `RunHandle` 保存协调器引用。
2. 实现子任务查询、取消和集成方法。
3. 在根运行结束后保持只读快照可查询，对无效控制返回明确错误。
4. 添加查询顺序、单独取消、集成和完成后调用测试。

**验证：** 运行 `uv run pytest -q tests/integration/test_sdk.py -k subagent`，期望全部通过。

## T28：导出公共类型

**文件：** `src/windcode/types.py`、`src/windcode/domain/subagents.py`  
**依赖：** T2、T3、T27

**步骤：**
1. 导出公开的子任务状态、记录、结果和生命周期事件。
2. 保持现有导出名称和行为不变。
3. 添加公共导入和类型可发现性测试。

**验证：** 运行 `uv run pytest -q tests/integration/test_sdk.py -k public`，期望全部通过。

## T29：实现子任务 TUI 组件

**文件：** `src/windcode/tui/widgets/subagents.py`、`src/windcode/tui/widgets/__init__.py`、`src/windcode/tui/styles.tcss`  
**依赖：** T3

**步骤：**
1. 实现固定尺寸的子任务组和状态行组件。
2. 展示角色、任务名、状态、耗时、用量和最近活动。
3. 展开详情展示摘要、提交、验证和遗留 Worktree。
4. 为排队、运行、阻塞、失败、冲突和完成提供文字与颜色双重状态。
5. 增加窄终端换行和无重叠样式。

**验证：** 运行 `uv run python -m py_compile src/windcode/tui/widgets/subagents.py`，期望退出码为 0。

## T30：接入 TUI 子事件与来源审批

**文件：** `src/windcode/tui/app.py`、`src/windcode/tui/widgets/approval.py`、`tests/integration/tui/test_subagents.py`  
**依赖：** T14、T27、T29

**步骤：**
1. 把子生命周期事件路由到对应子任务组。
2. 来源审批显示子任务名、角色、工具和风险，并通过现有响应路径提交。
3. 根会话切换、恢复和完成时正确重建或保留子任务展示。
4. 添加实时状态、并发审批和遗留状态测试。

**验证：** 运行 `uv run pytest -q tests/integration/tui/test_subagents.py -k 'events or approval'`，期望全部通过。

## T31：增加 `/agents` 命令

**文件：** `src/windcode/tui/commands.py`、`src/windcode/tui/app.py`、`tests/integration/tui/test_commands.py`、`tests/integration/tui/test_subagents.py`  
**依赖：** T27、T29

**步骤：**
1. 把 `agents` 加入统一命令目录和中文说明。
2. 执行命令时读取 `RunHandle.subagents()` 并显示稳定排序的状态摘要。
3. 无活动任务时显示明确空状态，遗留任务仍可查看路径。
4. 添加解析、补全、活动列表和空状态测试。

**验证：** 运行 `uv run pytest -q tests/integration/tui/test_commands.py tests/integration/tui/test_subagents.py -k agents`，期望全部通过。

## T32：验证上下文和状态隔离

**文件：** `tests/integration/test_subagent_runtime.py`  
**依赖：** T18、T25、T26

**步骤：**
1. 构造包含父历史和兄弟私有标记的模拟运行。
2. 断言每个子模型请求只包含自己的自包含任务和有效项目指令。
3. 断言会话、工具状态、用量和失败互不串线。
4. 验证读取型任务写入尝试被拒绝。

**验证：** 运行 `uv run pytest -q tests/integration/test_subagent_runtime.py`，期望全部通过。

## T33：验证并发、FIFO 和聚合预算

**文件：** `tests/unit/test_subagent_coordinator.py`、`tests/integration/test_subagent_runtime.py`  
**依赖：** T18、T32

**步骤：**
1. 用同步屏障证明最多 4 个任务同时运行。
2. 释放槽后断言队首先启动，最终结果仍按输入顺序。
3. 验证第 9 个任务被拒绝且不产生部分创建。
4. 分别耗尽独立和聚合模型步骤、工具调用预算。

**验证：** 运行 `uv run pytest -q tests/unit/test_subagent_coordinator.py tests/integration/test_subagent_runtime.py -k 'concurrent or fifo or budget'`，期望全部通过。

## T34：验证 SDK 与 TUI 事件一致性

**文件：** `tests/integration/test_sdk.py`、`tests/integration/tui/test_subagents.py`  
**依赖：** T30、T31

**步骤：**
1. 用同一模拟任务收集 SDK 和 TUI 消费的生命周期事件。
2. 对比创建、排队、运行、审批、完成、失败、取消和用量语义。
3. 验证事件恢复后序号不重复且来源字段保留。

**验证：** 运行 `uv run pytest -q tests/integration/test_sdk.py tests/integration/tui/test_subagents.py -k event`，期望全部通过。

## T35：实现多智能体端到端场景

**文件：** `tests/e2e/test_multi_agent_coding_task.py`  
**依赖：** T20、T28、T34

**步骤：**
1. 创建临时 Git 仓库和确定性的模拟模型。
2. 并行运行一个读取任务和两个写入任务。
3. 验证写任务在不同 Worktree 提交且父工作区未提前改变。
4. 顺序集成两个提交并运行父级验证。
5. 断言最终报告、用量、清理和父子审计记录准确。

**验证：** 运行 `uv run pytest -q tests/e2e/test_multi_agent_coding_task.py`，期望全部通过。

## T36：执行完整质量验证

**文件：** 全部本轮文件  
**依赖：** T1-T35

**步骤：**
1. 运行格式检查并修复本轮格式问题。
2. 运行 Lint 和严格类型检查，修复所有本轮错误。
3. 运行完整测试套件，调查并修复回归。
4. 构建源码包和 Wheel。
5. 对照 `checklist.md` 前置条件确认实现已可进入验收。

**验证：** 依次运行 `uv run ruff format --check .`、`uv run ruff check .`、`uv run pyright`、`uv run pytest -q`、`uv build`，期望全部退出码为 0。

## 执行顺序

```text
T1 ──┬── T2 ── T3 ───────────────┬── T14 ───────────────┐
     │      ├── T5 ──────────────┤                      │
     │      └── T7 ─ T8 ─ T9 ─ T10 ─ T11 ─┬─ T12 ─┐   │
     │                                      └─ T13  │   │
     └── T6                                      │   │
T4 ────────────────────────────────┐              │   │
                                   ├─ T16 ─ T17 ─ T18 ─ T19 ─ T21
                                   │              ├─ T20
T15 ───────────────────────────────┘              │
                                                   ├─ T22 ─┐
                                                   └─ T23 ─┴─ T24
T25 ───────────────────────────────────────────────────────┐
T16 + T21 + T24 + T25 ── T26 ── T27 ── T28                │
T3 ── T29 ───────────────┬── T30 ──┐                       │
T27 ─────────────────────┴── T31 ──┴─ T34                  │
T18 + T25 + T26 ── T32 ── T33                              │
T20 + T28 + T34 ── T35                                    │
T1-T35 ────────────────────────────────────────────────── T36
```
