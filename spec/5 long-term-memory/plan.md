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

每个 Markdown 文件包含版本化 frontmatter、来源、证据、置信度、状态、激活策略、优先级
和正文。项目 ID 是
规范化绝对工作区路径的 SHA-256 前 24 位，不暴露完整路径到检索键。

## 分层上下文装配链路

1. SDK 打开时初始化记忆服务；禁用配置时不创建存储。
2. `baseline_context()` 读取当前可见的 active + always，按 priority 降序、更新时间降序
   稳定选择，默认执行 30 条与 6000 字符预算。用户画像默认进入此层。
3. `search_context(query)` 只检索 active + search。SQLite FTS5 搜索标题、摘要、正文、标签
   和证据；结果不足时读取作用域内记录进行
   词法补充，无空格中文查询使用二元词匹配。
4. 两层都应用作用域过滤：user 记忆跨项目可见，project 记忆必须匹配当前项目 ID；manual
   和非 active 状态不参与自动上下文。
5. 动态结果按相关性、置信度和更新时间排序，最多读取 `recall_limit` 条，并执行
   `recall_max_chars` 字符预算。
6. `build_context()` 合并并去重，使用两个明确标题。常驻层不含更新时间；两层均声明记忆
   不可信且不能覆盖更高优先级指令与当前代码事实。

```text
用户输入
  -> active + always -> priority/预算 -> 常驻约束区块
  -> active + search -> FTS5/中文词法 -> 动态预算 -> 操作记忆区块
  -> manual / 非 active -> 不自动注入
  -> 合并进入本次 system prompt
```

## 运行后写入链路

1. 稳定用户事实直接提交 active + always；显式“记住”请求先分类为用户画像、项目知识、
   经验、SOP 或
   参考资料，不能用“非项目即用户画像”的默认规则覆盖显式类型。
2. 用户明确指定的未验证经验保存为 candidate。自动经验遵循 GenericAgent 的
   “No Execution, No Memory”，仅当任务产生文件变更、成功验证，并包含可复用的问题、
   解决方法和适用条件时提交；普通检查通过不形成记忆。
3. 自动经验评估输出结构化 `should_store` 决策；模型调用失败、JSON 无效或缺少必要字段时
   默认拒绝保存。active 经验在存储层强制要求验证证据。
4. 提交前使用当前模型发起无工具、有限输出的 JSON 提炼请求，分别生成标题、规范摘要、
   高密度正文和标签；解析或调用失败时回退到确定性原文提取，且不改变主任务终态。
5. 同标题或同摘要的 active 经验视为重复：合并验证证据并增加成功计数，不创建第二条；
   同一运行的显式经验候选可在验证和评估通过后原地更新并激活。
6. `/memory` 打开 TUI 管理器并直接切换启用状态；管理操作原子更新 Markdown 后同步索引。
7. 项目知识通过结构化模型判定是否为跨多数任务需要的核心事实；明确肯定为 always/60，
   失败、不确定或普通事实为 manual/50。显式“每次都要记住/始终适用”可直接判定 always。
8. 显式 SOP 保存为 candidate + search。经验评估只有同时给出步骤、适用条件和验证证据时
   才额外生成 SOP candidate；确认前绝不自动注入，同标题 SOP 原地更新。

```text
任务完成
  -> 显式意图分类 / 自动经验硬门槛
  -> 保守结构化提炼或经验价值评估
  -> 敏感信息与 active 经验验证约束
  -> 去重更新
  -> candidate 或 active Markdown
  -> 同步 SQLite 索引并发布 MemoryEvent
```

## 用户主动查询链路

自动召回与主动查询是两条独立链路。自动召回在普通任务开始前静默选择上下文；主动查询由
用户意图触发可观察的只读工具调用，不能仅依赖已经注入 system prompt 的记忆片段。

1. 根运行在长期记忆启用时按当前 `MemoryService` 动态注册三个只读工具；子智能体不继承
   这些工具，也不获得任何记忆写接口。
2. 宽泛请求如“看看长期记忆”调用 `memory_list`；带主题的请求如“找 Ruff 相关经验”调用
   `memory_search`；查看唯一 ID 或前缀对应详情时调用 `memory_get`。
3. `memory_search` 默认查询 active，可显式过滤类型、作用域、状态和 activation；过滤条件下沉到 FTS 和
   中文词法补充，避免先截断结果再过滤造成漏检。
4. `memory_list` 按更新时间列出当前项目可见记录；`memory_get` 先在当前可见集合中解析唯一
   ID 前缀，因此即使知道完整 ID 也不能读取其他项目的项目记忆。
5. 工具输出是结构化 JSON，包含 ID、类型、作用域、状态、activation、priority、标题、摘要、标签、置信度、
   更新时间和证据；搜索与详情按字符预算截断。
6. 每次成功调用发布 `searched`、`listed` 或 `retrieved` MemoryEvent，TUI 使用独立中文标签
   展示工具动作。无结果时返回空集合，不转而调用文件或 Shell 工具。
7. 记忆禁用时不注册这三个工具，system prompt 要求准确说明不可用，禁止搜索工作区代替。

```text
用户主动查看长期记忆
  -> system prompt 意图路由
  -> memory_list / memory_search / memory_get
  -> scope + kind/status/activation 过滤（可显式查看 manual）
  -> 条数与字符预算
  -> 结构化工具结果 + MemoryEvent
  -> 模型基于真实结果回答
```

## 当前检索边界

- 当前使用关键词 FTS 与中文词法补充，不使用 embedding；语义相近但无共同词的表达可能
  漏召回。词法补充会读取作用域内候选记录，但只有命中且符合预算的 active 记录进入上下文。
- 分类开关控制新记忆提取；全局 `memory.enabled = false` 同时关闭初始化、提取和召回。
- 主动查询依赖模型遵循工具路由提示；斜杠命令 `/memory search` 仍提供不经过模型的确定性入口。
- Markdown 是事实源，SQLite 索引损坏时可重建；检索失败降级为空召回，不阻塞主任务。
- SQLite 索引 schema version 为 2；检测到旧版本时删除可丢弃索引并从 Markdown 重建。旧
  Markdown 缺少 activation/priority 时按 kind 确定性补齐，不调用模型也不改写事实源。

## 管理接口

- SDK 提供 `set_memory_activation(memory_id, activation)`，通用更新接口同时允许修改
  activation 与 priority，并在存储层验证 priority 为 0..100。
- `/memory activation ID always|search|manual` 只对 active 记录生效；候选必须先确认。
- TUI 列表显示 `kind · scope · status · activation`，详情显示 priority，并用三段式选择器
  修改 active 记录。修改后 Markdown 与索引同步，下一次运行立即使用新策略。

## 统一状态根与迁移

```toml
[storage]
project_state_root = ".windcode/state"
user_storage_root = "~/.local/state/windcode/state"
```

- SDK 显式传入 `state_root` 时优先使用显式值，主要用于嵌入调用和测试。
- 未显式传入且配置 `project_state_root` 时，相对路径以工作区解析；未配置时使用
  `user_storage_root`，其默认值为 `~/.local/state/windcode/state`。
- 首次迁移把整个用户状态根复制到目标同级临时目录，包含 sessions、traces、extensions、
  memory 和 worktrees。源目标普通文件清单（相对路径与大小）完全一致后原子重命名为目标。
- 目标已存在时视为已初始化并直接使用，不在每次启动做昂贵同步；源目录始终保留，清理是
  独立的显式运维操作。
- 为兼容升级，默认用户根尚不存在时以旧 `~/.local/state/windcode` 为来源；目标嵌套在旧根
  下时，临时目录放在旧根的同级，避免递归复制。
- `.windcode/state/` 默认被 Git 忽略。长期记忆固定使用 `<selected_state_root>/memory`，不再
  维护 `[memory]` 下的独立存储路径或回退链。

### Session 体积控制

- EventBus 集中定义 transient 类型：文本流增量、推理状态、工具进度和子智能体进度。
- transient 进入当前 bus 的内存队列，但默认不写 trace，也不追加 `sessions/*/events.jsonl`，
  其 sequence 保持 `None`；诊断时可显式开启 transient trace，durable sequence 仍连续。
- 同一 bus 有实时积压时订阅器直接消费队列，保留 TUI 流式体验；恢复 bus 队列为空时才从
  session 重放 durable 事件。
- assistant 最终文本已经持久化在 `conversation_message`，因此不保存 `text_delta` 不影响
  会话恢复。历史 session 暂不自动重写，避免一次升级破坏既有分支链。

### Trace 体积控制

- `trace.enabled` 同时应用于主运行和子智能体；关闭时不创建 trace 目录或文件。
- 默认 `include_transient_events = false`，保留生命周期、工具、审批、错误、重试和用量事件。
- 每个新 run 启动时按默认 14 天保留期清理过期 JSONL，再按最旧优先将既有文件控制在
  默认 100MB；不截断正在写入的 run，也不在升级时批量改写历史文件。

## 参考架构矩阵

| 来源 | 固定提交 | 参考点 | Windcode 采纳方式 |
|---|---|---|---|
| Mewcode | 本地项目 | `mewcode/memory/` | 双作用域 Markdown、渐进召回、陈旧提醒 |
| Codex | `9e552e9d` | `codex-rs/memories/write/` | 分阶段提炼、受控工作区、可审计存储 |
| Pi | `8479bd84` | `packages/coding-agent/src/core/` | 小内核、会话服务组合、扩展隔离 |
| GenericAgent | `b3ab8362` | `docs/part2/chapter10-13/` | 分层记忆、信息密度、经验反思 |
| Hermes | `4281151a` | `gateway/memory_monitor.py`、session/search 与 skills | 闭环提醒、历史召回、画像与 Skill 候选 |

完整仓库保存在 `/home/tingfeng/code/agent/`，实现只借鉴行为，不复制源码。
