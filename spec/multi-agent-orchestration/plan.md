# Windcode 临时多智能体并行编排 Plan

## 架构概览

在现有分层事件内核中增加一个进程内编排层，不改变模型适配器和单智能体循环的核心职责：

```text
TUI / SDK
    │
    ▼
Root AgentLoop
    │ 调用编排工具
    ▼
SubagentCoordinator
├── 容量队列与聚合预算
├── 子任务状态机
├── 审批与事件路由
├── ChildRuntimeFactory
│   └── 独立 AgentLoop / Session / EventBus / RunControl / ToolRegistry
└── WorktreeManager
    ├── 创建隔离分支与 Worktree
    ├── 检查提交和验证证据
    ├── cherry-pick 集成
    └── 清理或保留遗留工作区
```

### 根智能体

根智能体继续使用现有 `AgentLoop`。只有根运行时注册以下编排工具：

- `spawn_subagents`
- `list_subagents`
- `cancel_subagent`
- `integrate_subagent`

编排工具与普通工具共享参数校验、权限策略、结构化结果和事件机制。子智能体的工具注册表不包含编排工具，从结构上保证扁平拓扑。

### 编排协调器

`SubagentCoordinator` 归属于一次根运行，负责：

- 校验委派策略、自包含任务、角色、权限和容量
- 为任务分配稳定编号并维护 FIFO 队列
- 最多同时启动配置允许的子任务
- 管理单个子任务和父任务聚合预算
- 路由子任务事件、审批和取消
- 持久化父子关系及状态转换
- 按输入顺序生成批量结果
- 在根运行结束时取消未完成任务并处理 Worktree

协调器不执行模型循环，也不直接修改代码。

### 子运行时

每个子智能体通过 `ChildRuntimeFactory` 创建一套独立运行时对象：

- 独立会话目录、事件序列和运行标识
- 独立上下文、执行控制和预算
- 独立工具注册表与 Shell 沙箱
- 基于父权限上限和角色配置生成的策略
- 与父智能体相同的默认模型链，可按允许配置覆盖
- 重新从子工作区加载项目指令

子运行时不复制父会话消息，仅把规范化后的任务说明作为首条用户输入。

### 工作区策略

读取型任务使用父工作区路径，但角色工具集强制只读。写入型任务启动前检查：

1. 工作区属于有效 Git 仓库
2. 当前工作区没有已跟踪或未跟踪修改
3. Git Worktree 能力可用
4. 基线提交仍与创建请求一致

通过后，为每个任务创建独立分支和 Worktree。子智能体只在该路径运行。完成后必须有提交且工作区干净，才具备可集成资格。

### 集成策略

父智能体调用 `integrate_subagent` 后：

1. 确认子任务已完成且提交可用
2. 返回并展示提交摘要、Diff 统计和验证证据
3. 记录父分支集成前提交
4. 执行 `git cherry-pick`
5. 成功后运行父级验证并更新状态
6. 冲突时中止 `cherry-pick`，确认父分支恢复到集成前状态，保留子分支和 Worktree

协调器不会自行决定集成，必须由根智能体显式调用。

### 事件路由

子事件先写入自己的会话和轨迹，再由协调器转换为带 `parent_run_id`、`subagent_id`、`task_index` 和 `role` 的父级生命周期事件。详细工具参数仍遵守现有脱敏配置。

审批请求通过父 `RunHandle` 返回用户，响应再路由到对应子 `RunControl`。子智能体发起用户问答时不转发，而是终止为 `blocked`。

## 核心数据结构

### 配置

```python
class DelegationMode(StrEnum):
    EXPLICIT = "explicit"
    PROACTIVE = "proactive"


class SubagentConfig(StrictModel):
    mode: DelegationMode = DelegationMode.EXPLICIT
    max_tasks: int = 8
    max_concurrent: int = 4
    max_model_steps: int = 20
    max_tool_calls: int = 50
    max_runtime_seconds: float = 900.0
    max_total_model_steps: int = 80
    max_total_tool_calls: int = 200
```

系统常量 `HARD_MAX_TASKS = 16` 和 `HARD_MAX_CONCURRENT = 8` 不进入配置模型。可配置限额不得超过系统硬上限，聚合预算不得小于单个子任务预算。项目配置可以覆盖用户配置，但不能放宽父运行时施加的限制。

### 任务模型

```python
class SubagentRole(StrEnum):
    RESEARCHER = "researcher"
    WORKER = "worker"
    VERIFIER = "verifier"


class SubagentTaskKind(StrEnum):
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True, slots=True)
class SubagentTaskSpec:
    task_name: str
    role: SubagentRole
    kind: SubagentTaskKind
    goal: str
    context: str
    expected_output: str
    verification: tuple[str, ...]
    allowed_tools: frozenset[str] | None = None
    model: str | None = None
```

`task_name` 使用小写字母、数字和下划线，在一次父运行内唯一。目标、上下文、预期产物和验证要求均不能为空。`researcher` 与 `verifier` 只允许读取型任务；`worker` 可执行两类任务。任务工具列表只能收紧角色工具集，模型只能引用已注册模型。

### 角色定义

```python
@dataclass(frozen=True, slots=True)
class RolePolicy:
    role: SubagentRole
    default_tools: frozenset[str]
    allowed_kinds: frozenset[SubagentTaskKind]
    system_instructions: str
```

| 角色 | 默认能力 | 主要职责 |
|---|---|---|
| `researcher` | 文件读取、匹配、搜索、只读 Shell | 代码探索与资料整理 |
| `worker` | 角色允许的完整编码工具 | 在隔离 Worktree 中实现并验证 |
| `verifier` | 文件读取、搜索、只读验证命令 | 独立检查实现和测试证据 |

角色策略由代码内建，不从项目文件动态加载。

### 状态模型

```python
class SubagentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"
    INTEGRATION_FAILED = "integration_failed"
    INTEGRATED = "integrated"
```

```text
QUEUED ──→ RUNNING ──→ BLOCKED
  │            ├─────→ COMPLETED ──→ INTEGRATED
  │            │             ├────→ INTEGRATION_FAILED
  │            ├─────→ FAILED
  │            └─────→ CANCELLED
  └──────────────────→ CANCELLED

COMPLETED ──集成冲突──→ CONFLICT
```

`CONFLICT` 和 `INTEGRATION_FAILED` 在首版中保持终态，父智能体在主工作区单独处理并重新验证，不能把原子任务改写为成功。父级验证失败时保留已集成提交和子 Worktree，不通过破坏性重置掩盖现场。其他终态记录同样不可回退或覆盖；补充工作创建新任务。

### 持久化记录

```python
@dataclass(frozen=True, slots=True)
class SubagentRecord:
    subagent_id: str
    parent_session_id: str
    parent_run_id: str
    child_session_id: str | None
    task_index: int
    spec: SubagentTaskSpec
    status: SubagentStatus
    base_commit: str | None
    branch: str | None
    worktree_path: Path | None
    commit: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_category: str | None
    error_message: str | None
```

记录保存在父会话的追加式事件中；子会话继续使用独立目录。路径恢复时重新验证其存在性和 Git 归属，不能盲目信任旧记录。

### 结果模型

```python
@dataclass(frozen=True, slots=True)
class VerificationResult:
    command: str
    exit_code: int | None
    output_summary: str
    passed: bool


@dataclass(frozen=True, slots=True)
class SubagentResult:
    subagent_id: str
    task_name: str
    status: SubagentStatus
    summary: str
    changed_files: tuple[str, ...]
    commit: str | None
    verification: tuple[VerificationResult, ...]
    usage: Usage
    error_category: str | None = None
    error_message: str | None = None
```

批量结果使用 `tuple[SubagentResult, ...]`，按 `task_index` 排序。

### 运行与预算模型

```python
class AggregateBudget:
    def consume_model_step(self) -> None: ...
    def consume_tool_calls(self, count: int) -> None: ...
    def check_runtime(self) -> None: ...
    def usage(self) -> AggregateUsage: ...


@dataclass(slots=True)
class ChildRuntime:
    record: SubagentRecord
    control: RunControl
    event_bus: EventBus
    task: asyncio.Task[SubagentResult]


class ApprovalRouter:
    async def request(
        self,
        subagent_id: str,
        request: PolicyRequest,
        decision: PolicyDecision,
    ) -> ApprovalChoice: ...

    def respond(self, response: ApprovalResponse) -> None: ...
    def cancel(self, subagent_id: str) -> None: ...
```

`AggregateBudget` 的计数操作由锁保护，并在消费前检查上限。`ApprovalRouter` 为父请求和子请求保存一对一映射，响应后立即删除映射。

### Git 与集成模型

```python
@dataclass(frozen=True, slots=True)
class GitBaseline:
    repository: Path
    branch: str
    commit: str


@dataclass(frozen=True, slots=True)
class WorktreeLease:
    subagent_id: str
    path: Path
    branch: str
    base_commit: str


@dataclass(frozen=True, slots=True)
class WorktreeResult:
    clean: bool
    commit: str | None
    changed_files: tuple[str, ...]
    diff_stat: str


@dataclass(frozen=True, slots=True)
class IntegrationResult:
    integrated: bool
    parent_commit_before: str
    parent_commit_after: str
    conflict_files: tuple[str, ...]
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class CleanupResult:
    removed: bool
    retained_path: Path | None
    reason: str | None
```

所有路径在每次破坏性操作前重新解析和校验。`IntegrationResult.integrated` 为 `False` 时，父分支必须位于 `parent_commit_before`，否则集成操作本身判定失败并保留现场。

## 核心接口

### 编排接口

```python
class SubagentCoordinator:
    async def spawn(
        self,
        specs: tuple[SubagentTaskSpec, ...],
    ) -> tuple[SubagentRecord, ...]: ...

    def list(self) -> tuple[SubagentRecord, ...]: ...

    async def cancel(self, subagent_id: str) -> SubagentRecord: ...

    async def integrate(
        self,
        subagent_id: str,
        verification_commands: tuple[str, ...],
    ) -> SubagentResult: ...

    async def shutdown(self, reason: str) -> None: ...

    async def recover(self) -> tuple[SubagentRecord, ...]: ...
```

`spawn()` 在返回前完成整批参数和总量校验，避免部分创建。任务进入队列后异步运行，完成事件主动进入父事件流。`integrate()` 只接受已完成且具备干净提交的写任务，并在同一次已审计操作中执行 `cherry-pick` 和父级验证。

### 子运行时工厂

```python
class ChildRuntimeFactory:
    def create(
        self,
        record: SubagentRecord,
        *,
        workspace: Path,
        role_policy: RolePolicy,
        parent_permission: PermissionMode,
        aggregate_budget: AggregateBudget,
        approval_router: ApprovalRouter,
    ) -> ChildRuntime: ...
```

工厂为每个子任务创建新的工具注册表、策略、沙箱、控制器、事件总线和会话，不复用根运行中已被修改的实例。

### Worktree 接口

```python
class WorktreeManager:
    async def validate_parent(self, workspace: Path) -> GitBaseline: ...
    async def create(self, task_name: str, baseline: GitBaseline) -> WorktreeLease: ...
    async def inspect(self, lease: WorktreeLease) -> WorktreeResult: ...
    async def integrate(
        self,
        lease: WorktreeLease,
        commit: str,
    ) -> IntegrationResult: ...
    async def cleanup(self, lease: WorktreeLease) -> CleanupResult: ...
    async def recover(self, record: SubagentRecord) -> WorktreeLease | None: ...
```

Git 命令使用参数数组执行并禁用交互，不通过拼接 Shell 字符串调用。

### 父级验证接口

```python
class VerificationRunner:
    async def run(
        self,
        workspace: Path,
        commands: tuple[str, ...],
    ) -> tuple[VerificationResult, ...]: ...
```

`SubagentCoordinator.integrate()` 先调用 `WorktreeManager.integrate()` 完成 Git 集成，再通过 `VerificationRunner` 在父工作区运行已审批的命令。验证执行复用现有 Shell 沙箱、超时、取消和输出截断机制；`worktrees/` 不依赖工具层。

### SDK 控制面

```python
class RunHandle:
    async def cancel_subagent(self, subagent_id: str) -> None: ...
    async def integrate_subagent(
        self,
        subagent_id: str,
        *,
        verification_commands: tuple[str, ...] = (),
    ) -> SubagentResult: ...
    def subagents(self) -> tuple[SubagentRecord, ...]: ...
```

模型工具和 SDK 方法调用同一个协调器，不维护两套状态。`RunHandle.cancel()` 先通知协调器关闭全部子任务，再取消根运行。

## 模块设计

### 编排核心

`runtime/subagents/coordinator.py` 维护任务表、FIFO 队列、并发槽、状态转换和批量结果顺序。所有状态变化先持久化，再发布事件。任务完成后立即释放并发槽并启动队首任务。

### 子运行时工厂

`runtime/subagents/factory.py` 为每个任务重新构造工具注册表、策略、沙箱、预算、会话和事件总线。模型传输可共享无状态连接池，但会话相关对象不得共享。

### 角色与工具过滤

`runtime/subagents/roles.py` 内建三个不可变角色策略。有效工具集按以下交集计算：

```text
角色默认工具 ∩ 任务指定工具 ∩ 父运行可用工具
```

读取型任务额外移除具有工作区写入、越界写入或网络能力的工具。`researcher` 和 `verifier` 可以运行策略判定为只读的 Shell 命令，但其工作区以只读方式挂载，并由命令分类和策略层双重限制。

### 预算控制

`runtime/subagents/budgets.py` 提供并发安全的聚合计数器。子运行时每次开始模型步骤或预留工具调用前，同时检查独立预算和聚合预算。取消、失败和重试不会返还已消耗预算。

### 审批路由

`runtime/subagents/approvals.py` 把子审批包装为带来源信息的父级审批事件，维护父请求 ID 与子请求 ID 的映射。父响应只投递给对应子控制器。子用户问答请求转换为 `blocked` 结果。

### 父级验证

`runtime/subagents/verification.py` 适配现有 Shell 执行能力，在父工作区顺序运行集成请求携带的验证命令，并产生结构化 `VerificationResult`。它不负责 Git 操作或状态转换。

### Worktree 管理

`worktrees/` 封装 Git 探测、基线校验、创建、检查、集成、恢复和清理。Git 命令统一通过无交互参数数组运行，并使用异步锁保护父仓库级操作。不同 Worktree 中的验证可以并行，创建、集成和删除串行执行。

### 编排工具

四个工具只在根注册表注册：

| 工具 | 行为 | 副作用 |
|---|---|---|
| `spawn_subagents` | 批量校验并创建任务 | 模型成本；写任务创建 Worktree |
| `list_subagents` | 返回稳定排序的状态快照 | 无 |
| `cancel_subagent` | 取消一个运行中或排队任务 | 终止模型和工具 |
| `integrate_subagent` | 审查后集成提交并运行父级验证 | 修改 Git 分支、执行命令 |

`integrate_subagent` 必须经过现有权限策略，审批摘要包含提交、分支和验证命令。

### 事件与持久化

新增父级事件：

- `SubagentQueued`
- `SubagentStarted`
- `SubagentProgress`
- `SubagentBlocked`
- `SubagentCompleted`
- `SubagentFailed`
- `SubagentCancelled`
- `SubagentIntegrated`
- `SubagentConflict`
- `SubagentCleanup`

审批事件增加可选的子智能体来源字段。子会话保留完整工具事件，父会话只保存关联信息和脱敏进度摘要。新增记录使用独立载荷版本，不修改旧会话元数据结构。

### TUI 与 SDK

TUI 在对应工具块中显示子任务组，每行固定展示角色、任务名、状态、耗时和用量；展开后显示最近活动、结果、提交和验证。新增 `/agents` 命令查看当前及遗留任务。

SDK 在 `RunHandle` 上提供查询、取消和集成方法，并通过现有异步事件迭代器暴露新增事件。

## 模块交互

### 批量创建

```text
根模型调用 spawn_subagents
→ 整批参数、角色、权限、Git 和容量预检
→ 为所有任务分配稳定编号
→ 持久化 queued 记录
→ 前 4 个启动，其余进入 FIFO
→ 工具立即返回任务标识和初始状态
```

### 子任务执行

```text
取得并发槽
→ 创建子会话和独立运行时
→ 写任务创建 Worktree，读任务绑定只读工作区
→ 加载子工作区项目指令
→ 运行自包含任务
→ 持久化终态和结果
→ 向父事件流发送完成通知
→ 释放槽并启动队首任务
```

### 审批与阻塞

```text
子工具需要审批
→ 子请求暂停
→ 父事件流发布带来源的审批
→ 用户响应
→ 精确路由回对应子任务

子智能体请求用户澄清
→ 不显示交互问题
→ 子任务结束为 blocked
→ 父智能体收到原因并统一处理
```

### 写任务集成

```text
子任务验证并提交
→ Worktree 必须干净
→ 返回提交和证据
→ 根智能体检查后调用 integrate_subagent
→ 串行执行 cherry-pick
→ 在父工作区运行指定验证
→ 成功：标记 integrated 并清理
→ 冲突：abort、校验父 HEAD、标记 conflict、保留成果
```

### 取消与关闭

```text
取消单个任务
→ 排队任务直接 cancelled
→ 运行任务取消模型、审批等待和工具
→ 不影响兄弟任务

取消根运行或关闭 SDK
→ 停止接收新任务
→ 取消全部排队和运行任务
→ 等待清理
→ 保留所有未集成或不干净 Worktree
```

### 恢复

```text
打开旧父会话
→ 重建子任务记录
→ running/queued 统一恢复为 cancelled/interrupted
→ 校验遗留分支和 Worktree
→ 展示可恢复成果
→ 不启动模型、不执行集成、不清理含成果目录
```

## 文件组织

```text
src/windcode/
├── config/models.py
├── domain/
│   ├── events.py
│   └── subagents.py
├── runtime/
│   ├── loop.py
│   ├── prompts.py
│   └── subagents/
│       ├── __init__.py
│       ├── approvals.py
│       ├── budgets.py
│       ├── coordinator.py
│       ├── factory.py
│       ├── roles.py
│       └── verification.py
├── tools/
│   ├── builtins.py
│   └── subagents/
│       ├── __init__.py
│       ├── spawn.py
│       ├── list.py
│       ├── cancel.py
│       └── integrate.py
├── worktrees/
│   ├── __init__.py
│   ├── git.py
│   ├── manager.py
│   └── models.py
├── sdk.py
├── types.py
└── tui/
    ├── app.py
    ├── commands.py
    ├── styles.tcss
    └── widgets/subagents.py

tests/
├── unit/
│   ├── test_subagent_models.py
│   ├── test_subagent_roles.py
│   ├── test_subagent_budgets.py
│   ├── test_subagent_coordinator.py
│   ├── test_subagent_events.py
│   ├── test_subagent_verification.py
│   └── test_worktree_manager.py
├── integration/
│   ├── test_subagent_runtime.py
│   ├── test_subagent_approval.py
│   ├── test_subagent_cancellation.py
│   ├── test_subagent_integration.py
│   ├── test_subagent_recovery.py
│   └── tui/test_subagents.py
└── e2e/test_multi_agent_coding_task.py
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 执行后端 | 进程内独立运行时 | 复用事件、SDK、审批和取消机制 |
| 拓扑 | 单层父子关系 | 控制成本与生命周期复杂度 |
| 上下文 | 全新会话 + 自包含任务 | 避免历史噪声和跨任务污染 |
| 并发 | FIFO 队列 + 异步任务 + 信号量 | 结果确定且资源有界 |
| 工具接口 | 四个生命周期工具 | 足够支持异步观察和控制 |
| 角色 | 三种内建角色 | 首版行为稳定，不依赖插件发现 |
| 权限 | 父权限、角色和任务工具集取交集 | 防止子任务提权 |
| 写隔离 | Git Worktree + 独立分支 | 并行修改互不覆盖 |
| 集成 | `cherry-pick` + 冲突中止 | 提交边界清晰且可追溯 |
| 预算 | 子级独立预算 + 父级聚合预算 | 同时限制单任务和总体成本 |
| 审批 | 父会话集中路由 | 保持单一用户交互入口 |
| 持久化 | 父关联记录 + 独立子会话 | 兼顾审计、隔离与恢复 |
| 恢复 | 报告遗留状态但不续跑 | 不重放模型调用和副作用 |

## 依赖方向

```text
domain.subagents / domain.events
        ▲
        ├── worktrees
        ├── runtime.subagents.roles / budgets / approvals / verification
        │          ▲
        │          └── runtime.subagents.coordinator / factory
        │                       ▲
        │                       ├── tools.subagents
        │                       └── sdk / tui
        └── config
```

- `domain` 只定义类型和事件，不依赖运行时、工具、TUI 或 Git 实现。
- `worktrees` 只依赖领域模型和标准进程执行，不依赖编排工具或 TUI。
- 编排工具持有协调器协议，协调器不反向导入工具实现。
- SDK 负责组装根协调器和子运行时工厂，TUI 只消费公共事件与 SDK 方法。
- 父级验证通过注入的 `VerificationRunner` 完成，避免 Worktree 层依赖工具层。

## Spec 覆盖

| Spec 需求 | 架构归属 |
|---|---|
| F1 | 编排协调器、`spawn_subagents`、子运行时工厂 |
| F2 | 委派配置、根系统提示、运行状态展示 |
| F3 | 根专属工具注册、子工具过滤 |
| F4 | 容量配置、FIFO 队列、系统硬上限 |
| F5 | `SubagentTaskSpec` 校验 |
| F6 | 子运行时工厂、全新子会话 |
| F7 | 角色策略、工具交集、父权限上限 |
| F8 | 读取型角色策略、只读工作区和沙箱 |
| F9 | Git 基线校验、Worktree 租约 |
| F10 | Worktree 检查、`SubagentResult`、验证结果 |
| F11 | 集成工具、Worktree 管理、父级验证执行器 |
| F12 | 状态机、FIFO 编号、事件与结果排序 |
| F13 | 审批路由、阻塞转换 |
| F14 | 协调器取消、子控制器、SDK 关闭流程 |
| F15 | Worktree 清理、父记录恢复 |
| F16 | 父级生命周期事件、TUI 子任务组件、轨迹 |
| F17 | `RunHandle` 子任务方法、公共事件类型 |

## 测试设计

- 单元测试覆盖配置边界、任务校验、角色工具交集、状态转换、FIFO、公平释放、独立与聚合预算。
- 临时 Git 仓库覆盖脏工作区拒绝、Worktree 隔离、干净提交、成功集成、冲突中止、遗留保留和清理。
- 模拟模型覆盖 8 个任务、4 路并发、输入顺序结果、兄弟失败隔离和上下文不泄漏。
- 集成测试覆盖审批精确路由、单任务取消、父取消、工具取消、SDK 关闭和异常恢复。
- TUI 测试覆盖并行状态、展开详情、来源审批、`/agents` 和窄终端布局。
- 端到端场景执行“并行调研 + 两个隔离写任务 + 提交审查 + 顺序集成 + 父级验证”。
- 全量验证运行格式检查、Lint、严格类型检查、测试套件和构建。
