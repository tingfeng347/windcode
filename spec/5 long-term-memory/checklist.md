# Windcode 长期记忆 Checklist

- [x] 参考仓库已持久化到 `/home/tingfeng/code/agent/` 并记录提交 SHA。
- [x] Markdown 是事实源，SQLite FTS 可从 Markdown 重建。
- [x] 四类记忆、双作用域和五态生命周期已定义。
- [x] 稳定事实和用户偏好自动生效；经验只有携带执行验证证据才生效。
- [x] 显式经验不会误分为用户画像；未经验证时保持候选，普通检查通过不生成经验。
- [x] 标题、摘要、正文和标签由无工具模型侧请求结构化提炼；失败安全回退且不阻塞主任务。
- [x] 用户画像跨项目共享，项目知识严格按项目 ID 隔离。
- [x] 敏感信息在落盘前拒绝，经验必须包含验证证据。
- [x] SDK 提供列表、搜索、详情、创建、确认、拒绝、编辑、归档、删除、重建和导出。
- [x] TUI `/memory` 直接打开管理面板，可切换启用状态、查看、删除和重建索引。
- [x] 召回与候选事件沿用 EventBus 持久化语义。
- [x] 每次运行只召回相关 active 记忆，并执行项目范围、条数和字符预算过滤；不会全量注入。
- [x] 自然语言主动查询调用 `memory_search/list/get`，无结果或禁用时不退化为文件搜索。
- [x] 主动详情查询不能跨项目读取项目记忆，三个工具均为只读且不下放给子智能体。
- [x] 记忆、SDK、主动查询与 TUI 聚焦回归：`46 passed in 6.76s`；自动事实提取、中文相关问法
  召回和 `/memory` 开关场景通过。
- [x] 格式与 Lint 通过；新增 memory、SDK 和事件严格类型检查为 0 errors。
- [x] 构建成功生成 sdist 和 wheel；`git diff --check` 通过。

## 回归说明

- 排除既有 MCP discovery 文件后的完整回归：`417 passed, 3 skipped in 31.52s`。
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
