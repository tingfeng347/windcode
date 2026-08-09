# 重构路线图

## 工作方式

- 分支：`refactor/runtime-architecture`。
- 每个本地 conventional commit 可独立回滚，并通过当时启用的全部门禁。
- 不 push、不创建 `v*` 标签、不发布；版本保持 0.2.4。
- 每阶段完成标准审查和兼容/spec 审查，发现问题先修复再继续。

## 阶段 0：可信基线

- 将 `tests/` 纳入 Git，保持 `spec/` 本地只读。
- 修复收集错误、8 个失败和 61 个测试类型错误。
- 建立八组兼容测试、旧状态 fixture 和 100 条 AC 验证矩阵。
- CI 覆盖 Ruff、strict Pyright、pytest、build、Python 3.11/3.12 和跨平台运行。
- 加入可复现的架构指标脚本。

## 阶段 1：依赖方向

状态：已完成。

- 在使用者一侧定义窄协议；production 与 test adapter 共享真实 seam。
- 子代理工具不再依赖具体 `SubagentCoordinator`。
- extensions 不再依赖具体 scheduler。
- 消除 `extensions <-> runtime <-> tools` 顶层环和 13 模块环。
- 调度调用/结果/约束和子代理分类错误下沉为 domain contract；旧 runtime import
  保持同一类型对象。
- shell 进程执行器归入工具实现，删除放错层的 `runtime.process`，不保留双实现或转发壳。
- 架构门禁收紧为 0 个顶层/模块 SCC、0 条 `tools -> runtime` 和
  `extensions -> runtime` 反向依赖。
- Python 3.11/3.12 全量测试均为 596 passed/3 skipped；Ruff、strict Pyright、build 和
  公共沙箱契约通过。

## 阶段 2：运行装配

状态：已完成。

- 引入唯一、profile-driven `RunBuilder`。
- 父运行和子运行委托同一深模块，保留权限、预算、worktree 和生命周期差异。
- `Windcode.start_run` 只校验请求、调用 builder 并返回 handle。
- 收缩 `AgentLoop` 构造 interface，但不改变持久化和取消时序。
- 完成共享 `RunResources`、`AgentLoop` 七参数 Interface、`RunHandle` 下移，以及父会话、
  访问和子运行装配迁移。
- `RunBuilder.start()` 装配父运行，绑定后的 `RunBuilder.prepare_child(ChildRunProfile)` 装配
  子运行；Coordinator 只消费 profile callable。旧 `ChildRunScope` 与 factory 装配路径已删除。
- `ChildRunSupport` 只封装角色工具、sandbox、Skill 与 Prompt 准入；`child_execution` 只封装
  聚合预算、禁止直接提问和审批路由，不构造 session、resources 或 loop。
- `RunMemory` 集中工具观察、召回和完成提取；`ParentAccessBuilder` 集中权限、sandbox、
  根工具与会话审批；`ParentRun` 集中 MCP 启动屏障、Hook 顺序、终态和清理。
- `RunExtensionState` 在启动时固定 snapshot、共享 MCP runtime、catalog 和 selected tools；
  reload 后旧运行继续使用旧 generation，新运行使用新 generation。
- `Windcode.start_run` 只保留上下文 guard、`RunBuilder.start()` 和 handle 跟踪，C901 从 31
  降至 3；子运行装配从旧 `ChildRunScope.create` 的 24 降至
  `RunBuilder.prepare_child` 的 4。
- 顶层/模块 SCC、两类反向依赖和高 fan-out 模块数均未增加；全局构造参数门禁从 12
  收紧到 11。
- 新增解析 import 与赋值别名的 AST 边界测试，要求父/子 `RunResources` 与 loop 只能由
  `RunBuilder` 装配，并禁止 `ChildRunScope` 回流。Standards 与兼容/spec 审查指出的双装配
  及门禁绕过缺口已据此修复；未发现可证实的外部兼容回归。
- Python 3.11/3.12 全量测试均为 604 passed/3 skipped；Ruff、strict Pyright、sdist/wheel
  build 和架构门禁通过。构建验证同时修复 `.venv-*` 被错误打入 sdist 的缺陷。

## 阶段 3：扩展、Provider 与工具

- 把 application lifecycle 从 SDK/TUI 移到对应模块。
- 统一 before/policy/approval/sandbox/after 工具链，不新增 dispatcher。
- 保持 run-pinned extension snapshot、MCP 后台启动和缓存失效语义。

## 阶段 4：子代理协调

- 稳定 facade 内部分离队列调度、mailbox/轮次、执行、worktree 集成和恢复清理。
- 保留并发、FIFO、权限交集、取消传播、冲突停止和确定性结果顺序。

## 阶段 5：TUI adapter

- Provider、Memory、Extension、Session 命令调用应用 interface。
- TUI 只负责输入、投影、确认和渲染；快捷键及用户可见行为不变。

## 阶段 6：收尾

- 运行旧状态读取/追加/重开验证和全部外部协议假实现测试。
- 删除已无调用、非公开、已迁移且门禁通过的旧路径。
- 更新验证证据、迁移说明和 ADR；完成全矩阵审查。

## 暂停条件

出现无法按已确认权威顺序裁定的契约冲突、不可兼容的公开行为、破坏性数据迁移、需要
真实凭据或外部发布动作时暂停。普通实现 bug 在当前阶段修复并记录。
