# Windcode TUI 启动页与命令菜单 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/windcode/tui/commands.py` | 统一命令目录、中文说明、参数提示、筛选和解析 |
| 新建 | `src/windcode/tui/widgets/command_menu.py` | 命令菜单展示、游标和选中值 |
| 新建 | `src/windcode/tui/widgets/welcome.py` | `windcode` 品牌欢迎态和上下文信息 |
| 修改 | `src/windcode/tui/widgets/input.py` | 输入变化通知、菜单导航、补全、提交和关闭 |
| 修改 | `src/windcode/tui/widgets/__init__.py` | 导出新增 TUI 组件 |
| 修改 | `src/windcode/tui/app.py` | 组合组件、协调界面模式和命令菜单生命周期 |
| 修改 | `src/windcode/tui/styles.tcss` | 欢迎态、紧凑对话态、输入区、菜单和响应式样式 |
| 修改 | `tests/integration/tui/test_commands.py` | 命令目录、筛选和解析测试 |
| 新建 | `tests/integration/tui/test_command_menu.py` | 命令菜单与输入键盘交互测试 |
| 修改 | `tests/integration/tui/test_app.py` | 启动态、模式切换、会话命令和响应式布局测试 |
| 修改 | `tests/e2e/test_coding_task.py` | 完整编码任务界面回归 |

## T1：建立统一命令目录

**文件：** `src/windcode/tui/commands.py`、`tests/integration/tui/test_commands.py`

**依赖：** 无

**步骤：**

1. 定义不可变的命令元数据，包含名称、中文说明和参数提示。
2. 使用稳定顺序声明现有八个命令，派生可解析命令集合。
3. 实现斜杠前缀筛选，拒绝非斜杠输入、含参数输入和无匹配输入。
4. 让完整命令解析复用统一目录，保持现有参数拆分和错误文案。
5. 添加完整目录、部分前缀、大小写、无匹配和严格解析测试。

**验证：** 运行 `uv run pytest tests/integration/tui/test_commands.py -q`；期望命令目录、筛选和解析测试全部通过。

## T2：实现命令菜单组件

**文件：** `src/windcode/tui/widgets/command_menu.py`、`src/windcode/tui/widgets/__init__.py`、`tests/integration/tui/test_command_menu.py`

**依赖：** T1

**步骤：**

1. 创建命令菜单组件并维护候选、游标和可见状态。
2. 实现显示、隐藏、向上、向下和读取选中值接口。
3. 将候选渲染为命令/参数提示与中文说明两列。
4. 为选中行增加前导符号和反色强调，限制游标不越界。
5. 添加空候选、单候选、多候选、边界导航和隐藏清理测试。

**验证：** 运行 `uv run pytest tests/integration/tui/test_command_menu.py -q -k menu`；期望菜单状态和渲染测试全部通过。

## T3：扩展输入组件的斜杠交互

**文件：** `src/windcode/tui/widgets/input.py`、`tests/integration/tui/test_command_menu.py`

**依赖：** T2

**步骤：**

1. 监听输入文本变化并发布斜杠菜单更新消息。
2. 增加 Tab、Esc、Up 和 Down 的高优先级键盘绑定。
3. 菜单可见时让 Up/Down 移动游标，Tab 把选中命令写入输入框。
4. 菜单可见时让 Enter 提交选中命令；菜单隐藏时保持现有提交逻辑。
5. Esc 关闭菜单并确保输入框继续聚焦。
6. 添加 `/`、`/mo`、无匹配、Tab、Enter、Esc 和多行普通文本测试。

**验证：** 运行 `uv run pytest tests/integration/tui/test_command_menu.py -q -k input`；期望全部输入和键盘行为测试通过。

## T4：实现 Windcode 欢迎视图

**文件：** `src/windcode/tui/widgets/welcome.py`、`src/windcode/tui/widgets/__init__.py`、`tests/integration/tui/test_app.py`

**依赖：** 无

**步骤：**

1. 创建欢迎视图，使用终端安全字符绘制居中的 `windcode` ASCII 标识。
2. 展示当前模型、权限、沙箱和工作区上下文，辅助文案全部使用中文。
3. 提供上下文更新接口，处理未显式指定模型的情况。
4. 添加品牌、中文状态、工作区和窄终端内容可见性测试。

**验证：** 运行 `uv run pytest tests/integration/tui/test_app.py -q -k welcome`；期望欢迎视图内容测试全部通过。

## T5：组合界面模式与菜单生命周期

**文件：** `src/windcode/tui/app.py`、`tests/integration/tui/test_app.py`

**依赖：** T2、T3、T4

**步骤：**

1. 在应用结构中组合欢迎视图、紧凑标题、消息流、命令菜单、输入框和状态栏。
2. 增加统一的 `welcome/chat` 模式切换，挂载时根据会话状态选择模式。
3. 处理斜杠菜单更新消息并调用统一命令筛选。
4. 普通消息提交前切换到对话态并关闭菜单。
5. `/new` 清空会话后切换欢迎态；恢复会话选择完成后切换对话态。
6. 所有命令执行、错误和普通提交路径统一关闭命令菜单并恢复输入焦点。
7. 添加新会话、首条提交、`/new`、恢复会话和菜单残留测试。

**验证：** 运行 `uv run pytest tests/integration/tui/test_app.py -q`；期望应用模式和现有双轮对话测试全部通过。

## T6：完成视觉系统和响应式布局

**文件：** `src/windcode/tui/styles.tcss`、`tests/integration/tui/test_app.py`

**依赖：** T5

**步骤：**

1. 定义深色中性背景、冷蓝主强调、琥珀状态和可读错误颜色。
2. 欢迎态将品牌与输入区域形成居中的单一视觉组，并保留底部上下文状态。
3. 对话态压缩标题高度，扩大消息区，保持输入区固定且不覆盖消息。
4. 命令菜单放在输入框正上方，与输入区共享左侧强调线并限制最大高度。
5. 调整用户消息、助手消息、推理完成状态和工具块的间距与层级。
6. 为 40×24 窄终端降低欢迎标识高度、菜单列宽和水平内边距。
7. 增加 40×24、80×24、120×36 的区域边界和不重叠断言。

**验证：** 运行 `uv run pytest tests/integration/tui/test_app.py tests/integration/tui/test_command_menu.py -q`；期望三个尺寸的布局及菜单测试全部通过。

## T7：回归现有完整交互

**文件：** `tests/e2e/test_coding_task.py`

**依赖：** T5、T6

**步骤：**

1. 更新受欢迎态和新增组件影响的现有 TUI 查询与等待逻辑。
2. 验证普通编码任务首次提交会切换到对话态。
3. 验证工具、审批、完成状态和第二轮消息仍按现有事件语义显示。
4. 确认任务执行期间命令菜单不会遮挡审批或提问组件。

**验证：** 运行 `uv run pytest tests/e2e/test_coding_task.py -q`；期望完整编码任务全部通过。

## T8：执行视觉验收和全量门禁

**文件：** 不新增实现文件；根据验收结果修正 T1-T7 涉及文件

**依赖：** T1-T7

**步骤：**

1. 使用 Textual headless 驱动分别生成欢迎态、命令菜单态和对话态快照。
2. 检查 40×24、80×24、120×36 下品牌、输入、菜单、消息和状态无重叠。
3. 检查 `/` 菜单从出现、筛选、导航、补全、执行到关闭的完整键盘流程。
4. 运行格式、Lint、严格类型检查、TUI 集成测试和全量测试。
5. 逐项记录 `checklist.md` 的实际验证证据，不以代码存在代替行为验收。

**验证：** 运行 `uv run ruff format .`、`uv run ruff check .`、`uv run pyright`、`uv run pytest tests/integration/tui tests/e2e/test_coding_task.py -q` 和 `uv run pytest -q`；期望全部通过且快照无布局缺陷。

## 执行顺序

```text
T1 -> T2 -> T3 ─┐
                 ├-> T5 -> T6 -> T7 -> T8
T4 ──────────────┘
```
