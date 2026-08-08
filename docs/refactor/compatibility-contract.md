# 重构兼容契约

权威顺序：明确确认的产品意图、最终版 spec 验收标准、用户可观察行为、自动化测试、
实现细节。过程文档中的旧状态和已确认 bug 不构成兼容要求。

## 契约组

1. **版本与入口**：Python 3.11+、`windcode` console script、根包和 stable types 导出。
2. **CLI**：主命令、extensions 子命令、参数默认值、错误分类和退出码。
3. **TUI**：12 个内建命令、memory/extensions 子命令、队列、确认、快捷键和审批语义。
4. **SDK**：`Windcode.open`、`start_run`、`RunHandle`、session、memory、extension 和 provider
   的公开 interface。
5. **配置**：严格 schema、层级合并、别名，以及 `AppConfig()`、首次用户配置和示例配置
   三套分别兼容的默认值。
6. **持久化**：Session v1、Extension state v1、Memory Markdown/SQLite v2、trace 和 auth。
7. **扩展协议**：capability ID、scope/shadow、Plugin manifest、Skill frontmatter、Hook 和 MCP。
8. **错误与事件**：错误分类/retry/fallback、30 类事件、durable/transient 和完整 round trip。

## 变更政策

- 本次尽量保留现有 facade。确需替换时在 0.3 标记弃用，最早 0.4 删除。
- 阶段 0 到阶段 5 冻结持久化格式。确需迁移时使用版本化 reader/migrator、原子写入和
  可恢复备份，并以旧版 fixture 验证。
- golden 只有在明确接受产品行为变化后才能更新。
- bug 修复记录旧行为、正确契约、证据和兼容影响，不与纯结构提交混合。
- 测试只使用匿名合成 fixture 和临时 `state_root`，不读取真实 `.windcode` 数据。

## 已确认的阶段 0 缺陷

- pytest 因缺少 `WindowsSandbox` 无法完整收集；排除该文件后有 8 个失败。
- 测试纳入 strict Pyright 后有 61 个错误，集中在 5 个文件。
- `ApprovalRequested` round trip 丢失 7 个授权字段。
- 包版本为 0.2.4，但 Plugin manifest 兼容表达式只接受 0.1.x 或 `*`。
- memory extraction 的 `successful_actions` 调用与当前 interface 漂移。

这些项目按契约修复，不保留错误行为。
