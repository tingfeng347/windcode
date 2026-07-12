# Windcode 长期记忆 Checklist

- [x] 参考仓库已持久化到 `/home/tingfeng/code/agent/` 并记录提交 SHA。
- [x] Markdown 是事实源，SQLite FTS 可从 Markdown 重建。
- [x] 五类记忆、三种激活策略、双作用域和五态生命周期已定义。
- [x] 稳定事实和用户偏好自动生效；经验只有携带执行验证证据才生效。
- [x] 显式经验不会误分为用户画像；未经验证时保持候选，普通检查通过不生成经验。
- [x] 标题、摘要、正文和标签由无工具模型侧请求结构化提炼；失败安全回退且不阻塞主任务。
- [x] 用户画像跨项目共享，项目知识严格按项目 ID 隔离。
- [x] 敏感信息在落盘前拒绝，经验必须包含验证证据。
- [x] SDK 提供列表、搜索、详情、创建、确认、拒绝、编辑、归档、删除、重建和导出。
- [x] TUI `/memory` 直接打开管理面板，可切换启用状态、查看、删除和重建索引。
- [x] 召回与候选事件沿用 EventBus 持久化语义。
- [x] 每次运行注入预算内 active + always，并动态召回相关 active + search；manual 不自动注入。
- [x] 自然语言主动查询调用 `memory_search/list/get`，无结果或禁用时不退化为文件搜索。
- [x] 主动详情查询不能跨项目读取项目记忆，三个工具均为只读且不下放给子智能体。
- [x] 记忆、SDK、主动查询与 TUI 聚焦回归：`44 passed in 6.54s`；分层上下文、自动事实提取、
  中文相关问法召回和 `/memory` 管理场景通过。
- [x] user_profile、核心项目事实、普通事实、经验、SOP 和参考资料具有确定默认 activation/priority。
- [x] 常驻层按 priority/更新时间稳定截断，动态层独立执行 FTS/中文词法与条数/字符预算。
- [x] 显式 SOP 与经验晋升 SOP 保持候选；确认后才参与 search，同标题不重复创建。
- [x] SDK、命令、TUI 可切换 activation；主动工具可过滤并返回 activation 与 priority。
- [x] schema v2 索引可从缺少新字段的旧 Markdown 确定性重建，无需模型迁移。
- [x] session、trace、extension、memory 和 worktree 统一使用一个选定状态根；记忆位于 `memory/`。
- [x] 配置 `.windcode/state` 后完整迁移默认用户状态根；607 个文件、约 117MB 的实际源目标
  相对路径和大小校验一致，原用户目录保留。
- [x] 未配置 `project_state_root` 时使用默认 `~/.local/state/windcode/state`；显式 SDK state_root 优先。
- [x] 新 session 不持久化 text/reasoning/tool/subagent progress，最终消息和恢复事件保持 durable。
- [x] transient 默认不写 trace；trace 开关、显式诊断模式、过期与容量清理均有单元测试。
- [x] session、SDK、subagent 与 TUI 聚焦回归：`93 passed in 24.90s`。
- [x] 格式与 Lint 通过；新增 memory、SDK 和事件严格类型检查为 0 errors。
- [x] 构建成功生成 sdist 和 wheel；`git diff --check` 通过。

## 回归说明

- 排除既有 MCP discovery 文件后的完整回归：`430 passed, 3 skipped in 32.09s`。
- 完整测试仍有 1 个既有失败项：
  `test_project_mcp_process_requires_trust_and_explicit_reload`，单独复跑仍失败：测试期望
  `McpError: Connection closed`，当前扩展运行时未抛出；与长期记忆路径无交集。
- 全量 Pyright：新增代码为 0 errors；既有 `tests/integration/tui/test_app.py` 对 Textual
  renderable 的 `spans/style` 访问产生 27 个 unknown/attribute errors。

## 参考版本（2026-07-12）

- Codex：`9e552e9d15ba52bed7077d5357f3e18e330f8f38`
- Pi：`8479bd84743e8889f728acb21a62794102db0529`
- GenericAgent：`b3ab836278bba6b6a3d2744f0110573ab415b896`
- Hermes Agent：`4281151ae859241351ba14d8c7682dc67ff4c126`
- Mewcode：本地 `/home/tingfeng/code/mewcode`
