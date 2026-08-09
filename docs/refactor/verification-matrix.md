# 重构验证矩阵

状态：`pending`、`passing`、`manual`、`blocked`。阶段提交不得包含 `failing` 项。

| 组 | 自动化证据 | 当前基线 | 完成条件 |
| --- | --- | --- | --- |
| 版本/入口 | wheel 内容、import/export、入口 smoke | 版本四源一致；本地 build 通过 | Python 3.11/3.12 均通过 |
| CLI | argparse、help、退出码 golden | 部分覆盖 | 主命令和 extensions 全矩阵通过 |
| TUI | command/binding 快照、Textual pilot | 部分覆盖 | 交互、队列、确认、取消通过 |
| SDK | `inspect.signature`、stable exports、行为契约 | 部分覆盖 | 公开 interface 无意外变化 |
| 配置 | schema/default golden、层级矩阵 | 部分覆盖 | 三套默认和别名分别通过 |
| 持久化 | 旧版 fixture 读取/追加/重开 | Session/Auth/Memory/Extension golden 通过 | 所有格式兼容且不触碰真实状态 |
| 扩展协议 | Plugin/Skill/Hook/MCP valid/invalid fixture | 项目 MCP 信任、required 启动阻断、reload generation 隔离定向测试通过 | fake stdio/HTTP MCP 全通过 |
| 错误/事件 | 分类真值表、全字段 round trip | 30 类类型往返及 Approval 全字段通过 | 为其他非默认字段补齐逐类往返 |
| 质量 | Ruff、Pyright、pytest、build | Python 3.11/3.12: 592 passed/3 skipped；其余门禁通过 | 全绿且测试纳入 Git/CI |
| 架构 | AST 依赖、参数、复杂度脚本 | 1 个顶层环、13 模块环；防替换白名单生效 | 达到硬阈值 |

## 架构硬阈值

- 顶层和模块依赖强连通分量均为 0。
- 只允许一个显式 composition root 扇出大于 20。
- `sdk.py` 扇出不超过 8 个模块、4 个顶层区域。
- `AgentLoop` 构造参数不超过 10；其他构造函数不超过 12。
- 编排函数 `C901 <= 15`；公开 facade 方法 `<= 10`。
- parent/child 不得直接重复构造共享运行组件。
- 不新增 `tools -> runtime concrete` 或 `extensions -> runtime concrete` 依赖。

## 验收标准映射

历史六阶段共 100 条 AC，逐项证据及未决缺口见
[`acceptance-criteria-matrix.md`](acceptance-criteria-matrix.md)。安全、运行时、持久化和扩展
生命周期不得只依赖人工验证。覆盖率先记录基线，不设置可刷数值的统一阈值，后续只
允许单调提升。
