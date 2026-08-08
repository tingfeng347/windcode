# Windcode 架构诊断

基线提交：`0cf79f23b0fc`。本诊断只描述重构起点，不把历史实现形状当作目标。

## 目标

重构首先降低认知复杂度和模块耦合，其次减少代码量。CLI、TUI、SDK、配置、持久
状态、Plugin、Skill、MCP、错误和事件语义保持兼容。现有 bug 不是兼容契约。

## 现状

- 生产代码 152 个 Python 模块、22,158 行；测试 125 个测试模块、约 13,000 行。
- `extensions`、`runtime`、`tui` 占生产代码 50.4%。
- `sdk.py` 1,363 行；`Windcode.start_run` 约 616 行，复杂度 49，读取 36 个配置叶子。
- `SubagentCoordinator` 1,077 行；`WindcodeApp` 1,043 行；`AgentLoop.run` 264 行。
- 父运行和子运行有 19 个相同直接装配调用点。
- 顶层唯一依赖环是 `extensions <-> runtime <-> tools`；模块级依赖环包含 13 个模块。
- `windcode.sdk` 扇出到 36 个模块和 15 个顶层区域；`AgentLoop` 构造函数有 18 个参数。

问题不是功能模块数量，而是生命周期、顺序约束和应用编排集中在少数协调热点。简单
拆文件只会把接口变浅，调用者仍需理解全部实现细节。

## 必须保留的高风险语义

1. assistant tool call 必须先持久化；取消、预算和错误必须补齐 interrupted tool result。
2. extension snapshot 在单次 run 内不可变；reload 只影响新 run，并清理相关 MCP 缓存。
3. required MCP 后台启动不能阻塞普通消息；失败 server 相互隔离。
4. plugin effects、hook、policy、approval、sandbox 和 child permission 必须保持收紧链路。
5. durable/transient event、sequence、branch、recovery 和 trace 顺序不能改变。
6. memory candidate、SOP、经验晋升、预算和项目隔离规则不能退化成普通 CRUD。
7. parent/child cancellation、shutdown、worktree integration 和 conflict stop 顺序保持不变。

## 目标模块

### RunBuilder

`RunBuilder` 是深模块。其外部接口只接受已解析请求和 parent/child profile，返回可启动
的运行时结果。session、artifact、trace、event bus、sandbox、policy、tool registry、budget、
instructions、extensions 和 loop 的构造顺序属于实现，不泄漏给 SDK 或子代理。

父运行和子运行是两个真实 adapter，因此这个 seam 不是假设性抽象。child profile 继续
负责权限交集、聚合预算、角色限制和 worktree 隔离。

### 应用 facade

`Windcode` 保留兼容 interface，只委托 provider、extension、memory、session 和 run 模块。
TUI 只调用这些 interface 并投影事件。子代理工具依赖窄协议，不导入具体 coordinator。

## 明确拒绝

- 不新增第二套事件或 extension 生命周期。
- 不用 service locator、`Mapping[str, Any]` 依赖袋或 barrel export 隐藏耦合。
- 不复制参考框架代码；只采用小核心、状态与活动编排分离、能力准入梯度等概念。
- 不把远程插件市场、更新、回滚、卸载或延迟工具目录混入本次重构。
