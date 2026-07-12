# Windcode 长期记忆实现方案

## 架构

`MemoryService` 负责当前工作区边界和召回上下文；`MemoryStore` 负责 Markdown 事实源、
SQLite FTS 索引及生命周期；`MemoryEvent` 沿用 EventBus 的先持久化后发布语义。SDK 是
唯一公共管理入口，TUI `/memory` 只调用 SDK。

存储布局：

```text
<state_root>/memory/
├── records/<scope>/<kind>/<memory-id>.md
└── index.sqlite3
```

每个 Markdown 文件包含版本化 frontmatter、来源、证据、置信度、状态和正文。项目 ID 是
规范化绝对工作区路径的 SHA-256 前 24 位，不暴露完整路径到检索键。

## 数据流

1. SDK 打开时初始化记忆服务；禁用配置时不创建存储。
2. 新运行用用户输入执行 FTS 查询，只读取 active 记录并应用项目范围。
3. 有结果时构造有来源、时效警告和字符上限的系统上下文，并持久化 recall 事件。
4. 稳定用户事实直接提交 active；经验遵循 GenericAgent 的 “No Execution, No Memory”，
   仅在存在实际验证证据时提交。
5. 提交前使用当前模型发起无工具、有限输出的 JSON 提炼请求，分别生成标题、规范摘要、
   高密度正文和标签；解析或调用失败时回退到确定性原文提取，且不改变主任务终态。
6. `/memory` 打开 TUI 管理器并直接切换启用状态；管理操作原子更新 Markdown 后同步索引。

## 参考架构矩阵

| 来源 | 固定提交 | 参考点 | Windcode 采纳方式 |
|---|---|---|---|
| Mewcode | 本地项目 | `mewcode/memory/` | 双作用域 Markdown、渐进召回、陈旧提醒 |
| Codex | `9e552e9d` | `codex-rs/memories/write/` | 分阶段提炼、受控工作区、可审计存储 |
| Pi | `8479bd84` | `packages/coding-agent/src/core/` | 小内核、会话服务组合、扩展隔离 |
| GenericAgent | `b3ab8362` | `docs/part2/chapter10-13/` | 分层记忆、信息密度、经验反思 |
| Hermes | `4281151a` | `gateway/memory_monitor.py`、session/search 与 skills | 闭环提醒、历史召回、画像与 Skill 候选 |

完整仓库保存在 `/home/tingfeng/code/agent/`，实现只借鉴行为，不复制源码。
