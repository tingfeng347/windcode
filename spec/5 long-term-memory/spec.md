# Windcode 跨会话长期记忆 Spec

## 背景与目标

Windcode 已有可恢复会话、上下文压缩、多智能体和扩展运行时，但每个新会话仍从零开始。
本阶段增加本地优先、可审计和可撤销的长期学习闭环，覆盖用户画像、项目知识、经验与
显式参考资料与 SOP。自生成 Skill 仅形成候选，不自动安装或修改核心代码。

## 功能需求

- F1：记忆正文以 Markdown 保存，SQLite 仅保存可重建的元数据和 FTS 索引。
- F2：支持 `user_profile`、`project_knowledge`、`experience`、`sop`、`reference` 五类记忆，
  以及 candidate、active、rejected、archived、superseded 五种状态。
- F3：用户级记忆可跨项目召回；项目级记忆以规范化项目根目录摘要严格隔离。
- F4：稳定用户事实与偏好自动进入 active；经验只有携带实际执行验证证据才自动进入
  active。未验证推测保持候选或不写入。
- F5：记忆类型与激活策略正交。active + always 组成常驻层；active + search 以本次输入
  通过 FTS 与中文词法补充动态召回；manual 永不自动注入。两层分别受数量和字符预算限制。
- F6：记忆始终标记来源和时间；历史项目事实必须提示对照当前代码验证。
- F7：用户表达“我喜欢/偏好/习惯”等稳定事实时保存为用户画像；明确“记住”的请求须
  优先识别用户画像、项目知识、经验或参考资料，禁止将显式经验降级为用户画像。疑问句、
  瞬态状态和一次性事件不得保存。
- F8：显式但未经验证的经验保持 candidate。自动经验必须同时具备成功任务、文件变更、
  成功验证，以及明确的问题、解决方法和适用条件；普通检查通过不得形成经验。active 经验
  必须含实际验证证据，失败结果不得被记录为成功经验。
- F9：密钥、令牌、密码、私钥和高敏内容禁止写入正文及索引。
- F10：提供 SDK 和 TUI 管理入口；输入 `/memory` 直接打开面板，可查看记录、删除、重建
  索引并切换启用状态，开关持久化到项目配置。
- F11：项目知识默认存放在用户状态目录；显式导出后才可进入仓库。
- F12：禁用记忆后不扫描、不提炼、不召回，现有会话格式保持兼容。
- F13：召回只读取 active 状态；用户级记忆允许跨项目召回，项目级记忆只允许项目 ID
  匹配时召回。candidate、rejected、archived 和 superseded 不得注入模型上下文。
- F14：system prompt 分为“始终生效的用户与项目约束”和“与当前任务相关的操作记忆”。
  常驻层不输出更新时间以保持前缀稳定；全部内容均标记为不可信历史信息，不得覆盖系统
  安全策略、项目指令或当前代码事实。
- F15：提供 `memory_search`、`memory_list` 和 `memory_get` 三个仅根运行可用的只读工具。
  用户明确要求查看、搜索、列出、核对或回忆长期记忆时，模型必须调用相应工具并基于实际
  结果回答，禁止使用工作区文件搜索代替长期记忆查询。
- F16：主动查询继续执行 active 状态、用户/项目作用域、类型、条数和字符预算过滤；详情
  查询必须拒绝其他项目的项目记忆。查询无结果或记忆禁用时直接说明，不得扫描仓库兜底。
- F17：主动查询发布 `searched`、`listed` 或 `retrieved` MemoryEvent；TUI 将三个工具分别
  显示为“检索长期记忆”“列出长期记忆”和“读取长期记忆”。
- F18：每条记录包含 `activation` 与 `priority(0..100)`。缺失字段的旧 Markdown 确定性
  迁移：user_profile 为 always，experience/project_knowledge 为 search，reference 为 manual；
  SQLite schema v2 不兼容时删除索引并从 Markdown 重建。
- F19：新用户画像默认 always/80；核心项目事实 always/60，普通项目事实 manual/50；经验
  search/50；SOP search/70；参考资料 manual/40。项目核心性由保守模型判断，失败或不确定
  时为 manual；用户明确声明始终适用时可直接 always。
- F20：显式 SOP 与经验晋升 SOP 均先成为 candidate，确认后方可进入自动上下文。SOP 必须
  包含步骤、适用条件和验证证据；相同 kind、scope 与规范化标题原地更新，不重复创建。
- F21：SDK、`/memory activation <ID> <always|search|manual>` 和 TUI 支持确定性切换激活策略；
  候选、拒绝、归档和被替代记录无论 activation 为何都不进入自动上下文。
- F22：主动查询可查看当前项目可见的 manual 和非 active 记录，并支持 activation 过滤；
  工具结果返回 activation 与 priority，仍执行项目隔离和查询事件审计。
- F23：`[storage]` 使用统一状态根，长期记忆固定存放在 `<selected_state_root>/memory`，不再
  单独配置项目/用户记忆路径。session、trace、extension、memory 和 worktree 使用同一选择结果。
- F24：状态根优先级为显式 SDK `state_root`、已配置的 `project_state_root`、`user_storage_root`。
  `user_storage_root` 默认 `~/.local/state/windcode/state`；项目根未配置时始终使用用户根。
- F25：配置项目状态根时，相对路径以工作区解析；
  首次使用且目标不存在时，将默认 `~/.local/state/windcode` 的 sessions、traces、extensions、
  memory、worktrees 等全部内容复制到临时目录，按普通文件相对路径和大小校验后原子安装。
  新用户根不存在时兼容读取旧 `~/.local/state/windcode` 并执行相同迁移。
- F26：完整状态迁移不删除来源、不做持续双写；目标已存在时直接使用目标。迁移失败不得留下
  半成品正式目录，项目状态目录必须加入 Git 忽略。
- F27：`text_delta`、`reasoning_status`、`tool_progress` 和 `subagent_progress` 属于 transient
  事件：继续实时发送给当前订阅者，但默认不写 session 或 trace。恢复只重放 durable 事件，
  最终对话内容以 `conversation_message` 为准；transient 不占用 session sequence。诊断时可通过
  `include_transient_events` 临时开启 transient trace。
- F28：`trace.enabled` 确定性控制 trace 写入；每个新 run 启动时按 `retention_days` 删除过期
  JSONL，再按最旧优先将既有 trace 收敛到 `max_total_mb`。当前 run 不被中途截断或改写。

## 威胁模型与非功能需求

- Markdown、frontmatter、索引和召回内容均视为不可信输入，拒绝路径逃逸和符号链接。
- 索引损坏或检索失败降级为空召回，不阻塞编码任务；索引可从 Markdown 确定性重建。
- 写入采用临时文件、刷盘和原子替换；SQLite 使用事务和 WAL。
- 不引入 embedding 服务、网络遥测或默认 Git 写入。

## 验收标准

- AC1：会话 A 陈述的稳定用户偏好无需确认即可在会话 B 的相关中文问法中召回。
- AC2：项目 A 的项目知识无法从项目 B 查询或注入，用户画像仍可共享。
- AC3：删除同时移除 Markdown 和索引；删除或损坏索引后可从 Markdown 重建。
- AC4：敏感内容在文件创建前被拒绝，状态目录中不存在残留。
- AC5：未经验证的经验可以成为候选但不能激活；普通 `ruff check` 或 `pytest` 通过不产生
  经验，召回数量和内容不超过配置预算。
- AC6：SDK、事件序列化和 `/memory` 命令行为稳定，旧事件消费者可忽略新事件。
- AC7：格式、Lint、Pyright、全量无密钥测试和构建通过。
- AC8：一次运行始终注入预算内的 active + always，并只动态注入与当前输入相关的
  active + search；manual 与非 active 不自动进入上下文，其他项目的项目记忆不可见。
- AC9：自然语言“在长期记忆中看看”调用 `memory_list`；带主题的请求调用 `memory_search`；
  单条详情调用 `memory_get`。运行记录中存在对应工具与 MemoryEvent，且不出现替代性的
  `glob`、`grep`、`read_file` 或 `shell` 调用。
- AC10：禁用长期记忆时不注册主动查询工具，系统明确说明不可用；主动查询无法用完整 ID
  读取其他项目的项目记忆。
- AC11：常驻层按 priority、更新时间稳定截断，默认最多 30 条/6000 字符；动态层默认最多
  5 条/12000 字符。两层使用独立标题且不输出重复记录。
- AC12：旧 Markdown 与旧 SQLite 索引可无模型迁移；SOP 候选确认、activation 切换和重新
  运行后的上下文变化均可通过 SDK、命令与 TUI 验证。
- AC13：所有状态消费者使用同一根目录，记忆固定为其 `memory/` 子目录，不存在独立路径优先级。
- AC14：配置 `project_state_root` 后，CLI/TUI 的 session、trace、extension、worktree 和默认
  状态消费者统一使用项目状态根；未配置时使用默认用户根，显式 SDK state_root 保持兼容。
- AC15：同一 EventBus 的订阅者能按原始顺序收到实时积压（含 transient）；恢复创建的新 bus
  只重放 durable 记录，不会在终态后追加旧文本片段。
