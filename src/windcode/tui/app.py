from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from rich.text import Text as RichText
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.theme import Theme
from textual.widgets import Static

from windcode.config import AppConfig, PermissionMode
from windcode.domain.events import (
    ApprovalRequested,
    ApprovalResponse,
    SubagentEvent,
    ToolFinished,
    ToolProgress,
    ToolStarted,
    UserInputRequested,
    UserResponse,
)
from windcode.domain.messages import TextBlock, message_from_dict
from windcode.sdk import RunHandle, Windcode
from windcode.tui.commands import SlashCommand, complete_commands, parse_command
from windcode.tui.widgets import (
    ApprovalWidget,
    ChatInput,
    CommandMenu,
    MessageStream,
    QuestionWidget,
    SessionSelector,
    StatusBar,
    SubagentGroup,
    ToolBlock,
    WelcomeView,
)
from windcode.types import RunRequest


class WindcodeApp(App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = "Windcode"
    INLINE_PADDING = 0
    BINDINGS: ClassVar[list[BindingType]] = [
        ("ctrl+c", "cancel_or_quit", "取消"),
        ("ctrl+q", "quit", "退出"),
    ]

    def __init__(
        self,
        config: AppConfig,
        *,
        workspace: Path,
        model: str | None = None,
        session_id: str | None = None,
        permission_mode: str | None = None,
        state_root: Path | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.workspace = workspace
        self.model = model
        self.session_id = session_id
        self.permission_mode = permission_mode or config.permission.mode.value
        self.client = Windcode.open(config, state_root=state_root)
        self.handle: RunHandle | None = None
        self.tool_blocks: dict[str, ToolBlock] = {}
        self.approval_widgets: dict[str, ApprovalWidget] = {}
        self.session_selector: SessionSelector | None = None
        self.subagent_group: SubagentGroup | None = None
        self.compact_next_run = False
        self.ui_mode: Literal["welcome", "chat"] = "chat"

    def _display_model(self) -> str:
        if self.model:
            return self.model
        if self.config.primary_provider:
            provider = self.config.providers.get(self.config.primary_provider)
            if provider is not None:
                return provider.model
        return "按配置"

    def _make_banner(self) -> RichText:
        banner = RichText()
        banner.append("▣ ", style="bold color(75)")
        banner.append("windcode", style="bold color(252)")
        banner.append("  ·  ", style="color(240)")
        banner.append(self._display_model(), style="color(246)")
        banner.append("  ·  ", style="color(240)")
        banner.append(str(self.workspace), style="color(242)")
        return banner

    def compose(self) -> ComposeResult:
        yield Static(self._make_banner(), id="title-bar")
        yield WelcomeView(
            model=self._display_model(),
            permission=self.permission_mode,
            sandbox=self.config.sandbox.enabled,
            workspace=self.workspace,
            id="welcome-view",
        )
        yield MessageStream(id="chat-area")
        with Vertical(id="input-area"):
            yield CommandMenu(id="command-menu")
            yield ChatInput(placeholder="输入任务, 或输入 / 使用命令", id="chat-input")
            yield StatusBar(id="status-bar")

    async def on_mount(self) -> None:
        await self.client.__aenter__()
        self.register_theme(
            Theme(
                name="windcode",
                primary="#5fa8e8",
                secondary="#d9a557",
                warning="#d9a557",
                error="#e36d6d",
                background="#151719",
                surface="#1b2024",
                panel="#20262b",
                dark=True,
            )
        )
        self.theme = "windcode"
        self._set_ui_mode("chat" if self.session_id else "welcome")
        self.set_class(self.size.width < 60, "narrow")
        self.query_one("#chat-input", ChatInput).focus()
        self._update_status("idle")
        if self.session_id is not None and self.client.session_exists(self.session_id):
            await self._restore_session(self.session_id, announce=False)

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < 60, "narrow")

    async def on_unmount(self) -> None:
        if self.handle is not None and not self.handle.done:
            await self.handle.cancel()
        await self.client.aclose()

    def _update_status(self, state: str) -> None:
        self.query_one("#status-bar", StatusBar).set_state(
            model=self._display_model(),
            permission=self.permission_mode,
            sandbox=self.config.sandbox.enabled,
            state=state,
            delegation=self.config.subagents.mode.value,
        )
        self.query_one("#welcome-view", WelcomeView).set_context(
            model=self._display_model(),
            permission=self.permission_mode,
            sandbox=self.config.sandbox.enabled,
            workspace=self.workspace,
        )

    def _set_ui_mode(self, mode: Literal["welcome", "chat"]) -> None:
        self.ui_mode = mode
        welcome = self.query_one("#welcome-view", WelcomeView)
        welcome.set_context(
            model=self._display_model(),
            permission=self.permission_mode,
            sandbox=self.config.sandbox.enabled,
            workspace=self.workspace,
        )
        welcome.display = mode == "welcome"
        self.query_one("#title-bar", Static).display = mode == "chat"
        self.query_one("#chat-area", MessageStream).display = mode == "chat"
        self.set_class(mode == "welcome", "welcome-mode")

    def _hide_command_menu(self) -> None:
        self.query_one("#command-menu", CommandMenu).hide()

    async def _show_system_message(self, text: str, *, error: bool = False) -> None:
        if self.ui_mode == "welcome":
            self.query_one("#welcome-view", WelcomeView).show_notice(text, error=error)
            return
        await self.query_one("#chat-area", MessageStream).add_system_message(text, error=error)

    def _resolve_session_id(self, value: str) -> str:
        session_ids = tuple(session.session_id for session in self.client.list_sessions())
        if value in session_ids:
            return value
        matches = tuple(session_id for session_id in session_ids if session_id.startswith(value))
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError(f"未知会话: {value}")
        raise ValueError(f"会话短 ID 不唯一: {value}")

    def _resolve_record_id(self, value: str) -> str:
        if self.session_id is None:
            raise ValueError("回退前请先选择会话")
        record_ids = tuple(
            record.record_id for record in self.client.load_session_records(self.session_id)
        )
        if value in record_ids:
            return value
        matches = tuple(record_id for record_id in record_ids if record_id.startswith(value))
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError(f"未知记录: {value}")
        raise ValueError(f"记录短 ID 不唯一: {value}")

    async def _restore_session(self, session_id: str, *, announce: bool = True) -> None:
        messages = self.query_one("#chat-area", MessageStream)
        if self.handle is not None and self.handle.done:
            self.handle = None
        self.session_id = session_id
        self.compact_next_run = False
        self.subagent_group = None
        self._set_ui_mode("chat")
        await messages.load_history(self.client.load_session_messages(session_id))
        if announce:
            metadata = next(
                session
                for session in self.client.list_sessions()
                if session.session_id == session_id
            )
            await messages.add_system_message(f"已恢复会话: {metadata.summary or session_id}")
        self.query_one("#chat-input", ChatInput).focus()
        self._update_status("idle")

    @on(ChatInput.SlashMenuUpdate)
    def update_slash_menu(self, event: ChatInput.SlashMenuUpdate) -> None:
        menu = self.query_one("#command-menu", CommandMenu)
        matches = complete_commands(event.prefix) if event.prefix is not None else ()
        menu.show_commands(matches) if matches else menu.hide()

    @on(ChatInput.Submitted)
    async def submit_prompt(self, event: ChatInput.Submitted) -> None:
        value = event.text.strip()
        self._hide_command_menu()
        if not value:
            return
        if value.startswith("/"):
            try:
                await self._command(parse_command(value))
            except ValueError as exc:
                await self._show_system_message(str(exc), error=True)
            return
        if self.handle is not None and not self.handle.done:
            await self._show_system_message("已有任务正在运行")
            return
        messages = self.query_one("#chat-area", MessageStream)
        self._set_ui_mode("chat")
        await messages.add_user_message(value)
        await messages.begin_run()
        request = RunRequest(
            prompt=value,
            workspace=self.workspace,
            session_id=self.session_id,
            model=self.model,
            permission_mode=self.permission_mode,
            compact_before_run=self.compact_next_run,
        )
        self.handle = self.client.start_run(request)
        self.compact_next_run = False
        self._update_status("running")
        self.run_worker(self._consume(self.handle), group="run", exclusive=True)

    async def _consume(self, handle: RunHandle) -> None:
        messages = self.query_one("#chat-area", MessageStream)
        try:
            async for event in handle:
                self.session_id = event.session_id
                await messages.apply_event(event)
                if isinstance(event, ToolStarted):
                    block = ToolBlock(event)
                    self.tool_blocks[event.call_id] = block
                    await messages.mount_in_ai_row(block)
                elif isinstance(event, ToolProgress):
                    block = self.tool_blocks.get(event.call_id)
                    if block is not None:
                        block.progress(event)
                elif isinstance(event, ToolFinished):
                    block = self.tool_blocks.get(event.call_id)
                    if block is not None:
                        block.finish(event)
                elif isinstance(event, ApprovalRequested):
                    messages.pause_thinking(event.request_id)
                    await messages.begin_block()
                    widget = ApprovalWidget(event)
                    self.approval_widgets[event.request_id] = widget
                    await messages.mount(widget)
                elif isinstance(event, UserInputRequested):
                    await messages.begin_block()
                    await messages.mount(QuestionWidget(event))
                elif isinstance(event, SubagentEvent):
                    if self.subagent_group is None or not self.subagent_group.is_attached:
                        await messages.begin_block()
                        self.subagent_group = SubagentGroup()
                        await messages.mount_in_ai_row(self.subagent_group)
                    await self.subagent_group.apply_event(event)
                messages.scroll_end(animate=False)
            result = await handle.result()
            self._update_status(result.status)
        finally:
            try:
                self.query_one("#chat-input", ChatInput).focus()
            except NoMatches:
                pass

    @on(ApprovalWidget.Decision)
    async def approval_decision(self, event: ApprovalWidget.Decision) -> None:
        self.query_one("#chat-area", MessageStream).resume_thinking(event.request_id)
        widget = self.approval_widgets.pop(event.request_id, None)
        if widget is not None and widget.is_attached:
            await widget.remove()
        if self.handle is not None:
            await self.handle.respond(ApprovalResponse(event.request_id, event.decision))

    @on(QuestionWidget.Submitted)
    async def question_submitted(self, event: QuestionWidget.Submitted) -> None:
        if self.handle is not None:
            await self.handle.respond(UserResponse(event.request_id, event.answers))

    @on(SessionSelector.Selected)
    async def session_selected(self, event: SessionSelector.Selected) -> None:
        if self.handle is None or self.handle.done:
            if self.session_selector is not None:
                await self.session_selector.remove()
                self.session_selector = None
            await self._restore_session(event.session_id)

    async def _command(self, command: SlashCommand) -> None:
        messages = self.query_one("#chat-area", MessageStream)
        active = self.handle is not None and not self.handle.done
        if command.name == "quit":
            self.exit()
            return
        if command.name == "new":
            if active:
                raise ValueError("任务运行期间不能新建会话")
            self.session_id = None
            self.compact_next_run = False
            await messages.clear()
            self.subagent_group = None
            self._set_ui_mode("welcome")
            self.query_one("#welcome-view", WelcomeView).clear_notice()
            self.query_one("#chat-input", ChatInput).focus()
        elif command.name == "resume":
            if active:
                raise ValueError("任务运行期间不能恢复会话")
            if not command.arguments:
                if self.session_selector is not None:
                    await self.session_selector.remove()
                self.session_selector = SessionSelector(self.client.list_sessions())
                await self.query_one("#input-area", Vertical).mount(
                    self.session_selector, before="#chat-input"
                )
                self.session_selector.focus()
                await self._show_system_message("请选择要恢复的会话")
                return
            if len(command.arguments) != 1:
                raise ValueError("用法: /resume 会话ID")
            session_id = self._resolve_session_id(command.arguments[0])
            await self._restore_session(session_id)
        elif command.name == "history":
            if command.arguments:
                raise ValueError("用法: /history")
            if self.session_id is None:
                raise ValueError("请先选择会话")
            lines = ["当前会话历史节点:"]
            for record in self.client.load_session_records(self.session_id):
                if record.record_type != "conversation_message":
                    continue
                message = message_from_dict(record.payload)
                text = " ".join(
                    block.text for block in message.content if isinstance(block, TextBlock)
                )
                preview = " ".join(text.split())[:48]
                lines.append(
                    f"{record.sequence}. {record.record_id[:12]}  {message.role.value}  {preview}"
                )
            await self._show_system_message("\n".join(lines))
        elif command.name == "rewind":
            if active:
                raise ValueError("任务运行期间不能回退会话")
            if len(command.arguments) != 1:
                raise ValueError("用法: /rewind 记录ID")
            record_id = self._resolve_record_id(command.arguments[0])
            assert self.session_id is not None
            self.client.rewind_session(self.session_id, record_id)
            await self._restore_session(self.session_id, announce=False)
            await self._show_system_message(f"已回退到记录 {record_id[:12]}")
        elif command.name == "mode":
            if active:
                raise ValueError("任务运行期间不能切换权限模式")
            if len(command.arguments) != 1:
                raise ValueError("用法: /mode 模式")
            try:
                self.permission_mode = PermissionMode(command.arguments[0]).value
            except ValueError as exc:
                raise ValueError(f"未知权限模式: {command.arguments[0]}") from exc
            await self._show_system_message(f"权限模式: {self.permission_mode}")
        elif command.name == "model":
            if active:
                raise ValueError("任务运行期间不能切换模型")
            if len(command.arguments) != 1:
                raise ValueError("用法: /model 模型名称")
            self.model = command.arguments[0]
            self.query_one("#title-bar", Static).update(self._make_banner())
            self.query_one("#welcome-view", WelcomeView).set_context(
                model=self._display_model(),
                permission=self.permission_mode,
                sandbox=self.config.sandbox.enabled,
                workspace=self.workspace,
            )
            await self._show_system_message(f"模型: {self.model}")
        elif command.name == "compact":
            if command.arguments:
                raise ValueError("用法: /compact")
            if active and self.handle is not None:
                await self.handle.compact()
                await self._show_system_message("已请求压缩上下文")
            else:
                if self.session_id is None:
                    raise ValueError("压缩前请先选择会话")
                self.compact_next_run = True
                await self._show_system_message("将在下一轮模型请求前压缩上下文")
        elif command.name == "clear":
            if command.arguments:
                raise ValueError("用法: /clear")
            await messages.clear()
            self.subagent_group = None
        elif command.name == "help":
            if command.arguments:
                raise ValueError("用法: /help")
            lines = ["可用命令:"]
            for definition in complete_commands("/"):
                hint = f" {definition.argument_hint}" if definition.argument_hint else ""
                lines.append(f"/{definition.name}{hint}  {definition.description}")
            await self._show_system_message("\n".join(lines))
        elif command.name == "status":
            await self._show_system_message(
                f"会话: {self.session_id or '新会话'}  模型: {self._display_model()}  "
                f"权限: {self.permission_mode}  委派: {self.config.subagents.mode.value}"
            )
        elif command.name == "agents":
            if command.arguments:
                raise ValueError("用法: /agents")
            records = () if self.handle is None else self.handle.subagents()
            if not records:
                await self._show_system_message("当前没有子智能体任务")
            else:
                lines = ["子智能体任务:"]
                for record in records:
                    line = (
                        f"{record.task_index + 1}. {record.spec.task_name} · "
                        f"{record.spec.role.value} · {record.status.value}"
                    )
                    if record.worktree_path is not None:
                        line += f" · Worktree: {record.worktree_path}"
                    lines.append(line)
                await self._show_system_message("\n".join(lines))
        self._update_status("running" if self.handle and not self.handle.done else "idle")

    async def action_cancel_or_quit(self) -> None:
        if self.handle is not None and not self.handle.done:
            await self.handle.cancel()
            return
        self.exit()
