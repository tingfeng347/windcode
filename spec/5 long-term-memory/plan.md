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

## 运行前召回链路

1. SDK 打开时初始化记忆服务；禁用配置时不创建存储。
2. 新运行以本次用户输入作为查询，不把完整记忆库预加载到模型上下文。
3. SQLite FTS5 搜索标题、摘要、正文、标签和证据；结果不足时读取作用域内候选记录进行
   词法补充，无空格中文查询使用二元词匹配。
4. 查询只接受 active 状态，并应用作用域过滤：user 记忆跨项目可见，project 记忆必须匹配
   当前规范化项目 ID；其他生命周期状态不参与召回。
5. 结果按相关性、置信度和更新时间排序，最多读取 `recall_limit` 条，并在拼接时执行
   `recall_max_chars` 字符预算。
6. 有结果时构造包含类型、范围、更新时间和历史事实校验提示的记忆段，追加到本次运行的
   system prompt，并发布 recall 事件；没有结果时不增加记忆上下文。

```text
用户输入
  -> active + scope 过滤
  -> FTS5 / 中文词法补充
  -> 相关性排序
  -> 条数与字符预算
  -> 少量相关记忆进入 system prompt
```

## 运行后写入链路

1. 稳定用户事实直接提交 active；显式“记住”请求先分类为用户画像、项目知识、经验或
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
3. `memory_search` 默认查询 active，可显式过滤类型、作用域和状态；过滤条件下沉到 FTS 和
   中文词法补充，避免先截断结果再过滤造成漏检。
4. `memory_list` 按更新时间列出当前项目可见记录；`memory_get` 先在当前可见集合中解析唯一
   ID 前缀，因此即使知道完整 ID 也不能读取其他项目的项目记忆。
5. 工具输出是结构化 JSON，包含 ID、类型、作用域、状态、标题、摘要、标签、置信度、
   更新时间和证据；搜索与详情按字符预算截断。
6. 每次成功调用发布 `searched`、`listed` 或 `retrieved` MemoryEvent，TUI 使用独立中文标签
   展示工具动作。无结果时返回空集合，不转而调用文件或 Shell 工具。
7. 记忆禁用时不注册这三个工具，system prompt 要求准确说明不可用，禁止搜索工作区代替。

```text
用户主动查看长期记忆
  -> system prompt 意图路由
  -> memory_list / memory_search / memory_get
  -> active + scope + kind/status 过滤
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

## 参考架构矩阵

| 来源 | 固定提交 | 参考点 | Windcode 采纳方式 |
|---|---|---|---|
| Mewcode | 本地项目 | `mewcode/memory/` | 双作用域 Markdown、渐进召回、陈旧提醒 |
| Codex | `9e552e9d` | `codex-rs/memories/write/` | 分阶段提炼、受控工作区、可审计存储 |
| Pi | `8479bd84` | `packages/coding-agent/src/core/` | 小内核、会话服务组合、扩展隔离 |
| GenericAgent | `b3ab8362` | `docs/part2/chapter10-13/` | 分层记忆、信息密度、经验反思 |
| Hermes | `4281151a` | `gateway/memory_monitor.py`、session/search 与 skills | 闭环提醒、历史召回、画像与 Skill 候选 |

完整仓库保存在 `/home/tingfeng/code/agent/`，实现只借鉴行为，不复制源码。
