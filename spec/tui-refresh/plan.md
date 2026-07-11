# Windcode TUI 启动页与命令菜单 Plan

## 架构概览

本次改造保持现有 `WindcodeApp -> SDK -> Runtime` 单向依赖，只扩展 TUI 展示层：

```text
WindcodeApp
├── WelcomeView          新会话欢迎态与品牌信息
├── MessageStream        紧凑对话消息流
└── InputArea
    ├── CommandMenu      斜杠命令候选与键盘选中状态
    ├── ChatInput        输入、提交、补全和菜单导航事件
    └── StatusBar        模型、权限、沙箱和运行状态
```

应用维护单一界面模式：`welcome` 或 `chat`。新会话以 `welcome` 启动，普通消息提交前切到 `chat`；恢复会话直接使用 `chat`；执行 `/new` 后回到 `welcome`。

命令定义由一个目录提供名称、中文说明和参数提示，命令解析、命令菜单和补全共用同一数据源，避免可执行命令与菜单内容不一致。

## 核心数据结构

### `CommandDefinition`

不可变命令元数据：

```python
@dataclass(frozen=True, slots=True)
class CommandDefinition:
    name: str
    description: str
    argument_hint: str = ""

    @property
    def value(self) -> str: ...
```

- `name`：不含 `/` 的规范命令名。
- `description`：命令菜单使用的中文说明。
- `argument_hint`：需要参数时显示的简短提示，不参与命令名匹配。
- `value`：可插入输入框的 `/命令` 文本。

### `COMMAND_CATALOG`

`tuple[CommandDefinition, ...]`，包含现有八个命令，按稳定顺序展示：`new`、`resume`、`rewind`、`mode`、`model`、`compact`、`status`、`quit`。

### `UiMode`

```python
UiMode = Literal["welcome", "chat"]
```

应用只通过统一的模式切换入口控制欢迎态、消息区、紧凑标题和输入区域样式，避免各命令直接修改多个组件。

### `CommandMenu` 状态

```python
items: tuple[CommandDefinition, ...]
cursor: int
visible: bool
```

菜单只保存过滤后的候选和当前游标，不保存命令执行状态。候选变化时游标归零；隐藏时清空候选和游标。

## 核心接口

### 命令目录与筛选

```python
def complete_commands(prefix: str) -> tuple[CommandDefinition, ...]: ...
def parse_command(value: str) -> SlashCommand: ...
```

- `complete_commands` 仅匹配以 `/` 开头且不含空格的命令前缀。
- `parse_command` 继续负责完整提交后的严格校验，并复用命令目录判断命令是否存在。

### `ChatInput` 消息

```python
class SlashMenuUpdate(Message):
    prefix: str | None

class Submitted(Message):
    text: str
```

`ChatInput` 在文本发生变化后发布 `SlashMenuUpdate`。只有光标所在输入内容是首行斜杠命令前缀时传递前缀，否则传递 `None`。

### `ChatInput` 菜单操作

```python
action_submit()
action_complete()
action_nav_up()
action_nav_down()
action_dismiss_menu()
```

输入组件通过查询同一应用中的 `CommandMenu` 判断菜单状态：

- Enter：菜单可见时提交选中命令，否则提交当前文本。
- Tab：菜单可见时将选中命令写入输入框并保留输入焦点。
- Up/Down：菜单可见时移动游标；隐藏时不劫持现有输入行为。
- Esc：隐藏菜单并保持输入焦点。

### `CommandMenu`

```python
show(items: tuple[CommandDefinition, ...]) -> None
hide() -> None
move_up() -> None
move_down() -> None
selected_value() -> str | None
```

渲染为两列：命令及参数提示、中文说明。选中行同时使用反色/强调和前导符号，保证不只依赖颜色。

### `WelcomeView`

```python
set_context(*, model: str, permission: str, sandbox: str, workspace: Path) -> None
```

欢迎视图包含 ASCII `windcode` 标识和一行紧凑上下文信息。输入框仍由底部输入区域唯一持有，不在欢迎视图复制输入组件。

### 应用模式切换

```python
def _set_ui_mode(mode: UiMode) -> None
```

- `welcome`：显示欢迎视图，隐藏大型空消息区和紧凑标题。
- `chat`：隐藏欢迎视图，显示消息流和紧凑标题。
- 输入区域和状态栏在两种模式均存在，通过 CSS class 调整宽度、位置和边框。

## 模块设计

### 命令目录

**职责：** 统一维护可解析命令、中文说明、参数提示和前缀筛选。

**对外接口：** `COMMAND_CATALOG`、`complete_commands()`、`parse_command()`。

**依赖：** 仅标准库。

### 输入组件

**职责：** 发布输入变化、提交、补全、导航和关闭菜单事件；保持焦点与多行输入行为。

**对外接口：** Textual 消息和键盘 action。

**依赖：** 命令菜单的只读选中接口。

### 命令菜单

**职责：** 展示过滤结果、维护游标、返回当前选中命令。

**对外接口：** 显示、隐藏、移动和读取选择。

**依赖：** 命令元数据，不依赖应用命令执行逻辑。

### 欢迎视图

**职责：** 展示 `windcode` 品牌和当前工作上下文，适配不同终端尺寸。

**对外接口：** 更新上下文展示。

**依赖：** 不依赖 SDK 或会话存储。

### 应用协调器

**职责：** 处理输入变化、切换界面模式、执行命令并保证菜单生命周期完整。

**对外接口：** 现有应用事件处理器和新增菜单更新处理器。

**依赖：** 欢迎视图、命令菜单、输入组件、现有消息流和 SDK。

### 样式系统

**职责：** 定义欢迎态居中布局、紧凑对话态、输入框左侧强调线、菜单两列布局、选中态和窄终端适配。

**视觉决策：**

- 背景保持终端深色表面，不引入图片或渐变。
- 主强调色使用冷蓝，运行/推理补充状态使用琥珀色，错误保持红色，正文使用中性灰白。
- 欢迎态只保留一个主要视觉焦点：ASCII `windcode`。
- 对话态降低标题存在感，让消息、工具和审批成为主要内容。
- 输入区与菜单共享同一左侧强调线，菜单不使用嵌套卡片。

## 模块交互

### 新会话启动

```text
应用挂载
-> 更新 WelcomeView 上下文
-> 设置 welcome 模式
-> 聚焦 ChatInput
```

### 首次提交普通消息

```text
ChatInput.Submitted
-> 隐藏 CommandMenu
-> 设置 chat 模式
-> 添加用户消息
-> 启动现有 SDK RunHandle
-> 按现有事件流更新 MessageStream
```

### 斜杠菜单

```text
ChatInput 文本变化
-> SlashMenuUpdate(prefix)
-> complete_commands(prefix)
-> CommandMenu.show(matches) / hide()

Up/Down -> CommandMenu 移动游标
Tab -> 选中值写回 ChatInput
Enter -> 选中值进入现有命令提交链
Esc -> CommandMenu.hide()
```

### 会话命令

```text
/new -> 清空消息与会话 -> welcome 模式 -> 聚焦输入
/resume -> 选择会话 -> chat 模式 -> 聚焦输入
其他命令 -> 保持当前模式 -> 隐藏菜单
```

## 文件组织

```text
src/windcode/tui/
├── app.py                    - 应用模式和组件协调
├── commands.py               - 命令目录、筛选与解析
├── styles.tcss               - 欢迎态、对话态和命令菜单样式
└── widgets/
    ├── __init__.py           - 新组件导出
    ├── input.py              - 输入变化及菜单键盘操作
    ├── command_menu.py       - 命令候选菜单
    └── welcome.py            - Windcode 欢迎视图

tests/
├── integration/tui/
│   ├── test_app.py           - 启动态、模式切换和完整双轮行为
│   ├── test_commands.py      - 命令目录、筛选和解析
│   └── test_command_menu.py  - 菜单显示、筛选、导航和补全
└── e2e/test_coding_task.py   - 现有完整编码任务回归
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 欢迎态生命周期 | 首次普通提交后切换为紧凑态 | 接近 OpenCode 首屏体验，同时避免占用长期对话空间 |
| 输入组件 | 继续使用唯一的现有输入框 | 避免欢迎态和对话态之间迁移文本、焦点和事件订阅 |
| 命令数据源 | 单一命令目录 | 防止解析器、菜单和文档出现命令集合漂移 |
| 菜单实现 | 独立轻量组件 | 状态清晰，可独立测试，不将展示逻辑塞入输入组件 |
| 菜单位置 | 输入框正上方、共享左侧强调线 | 保持候选接近输入上下文并参考成熟终端 Agent 的操作路径 |
| 状态切换 | 应用级显式模式 | 新建、恢复和首次提交行为可预测，避免依赖组件是否为空推断 |
| 视觉资产 | ASCII 文字标识 | 适配终端环境，不引入位图、字体或终端透明度依赖 |
| 响应式 | CSS breakpoint 调整间距与标识尺寸 | 保持 40×24 到 120×36 的同一组件结构，减少分支布局 |
| 兼容策略 | 不改变 SDK 和事件协议 | 本轮仅解决 TUI 交互与视觉问题，控制影响范围 |
