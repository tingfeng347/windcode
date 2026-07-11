from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from rich.text import Text as RichText
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical
from textual.theme import Theme
from textual.widgets import Static

from windcode.config import AppConfig, PermissionMode
from windcode.domain.events import (
    ApprovalRequested,
    ApprovalResponse,
    ToolFinished,
    ToolProgress,
    ToolStarted,
    UserInputRequested,
    UserResponse,
)
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
        )
        self.handle = self.client.start_run(request)
        self._update_status("running")
        self.run_worker(self._consume(self.handle), group="run", exclusive=True)

    async def _consume(self, handle: RunHandle) -> None:
        messages = self.query_one("#chat-area", MessageStream)
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
                await messages.begin_block()
                widget = ApprovalWidget(event)
                self.approval_widgets[event.request_id] = widget
                await messages.mount(widget)
            elif isinstance(event, UserInputRequested):
                await messages.begin_block()
                await messages.mount(QuestionWidget(event))
            messages.scroll_end(animate=False)
        result = await handle.result()
        self._update_status(result.status)

    @on(ApprovalWidget.Decision)
    async def approval_decision(self, event: ApprovalWidget.Decision) -> None:
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
            self.session_id = event.session_id
            if self.session_selector is not None:
                await self.session_selector.remove()
                self.session_selector = None
            self.query_one("#chat-input", ChatInput).focus()
            self._set_ui_mode("chat")
            await self.query_one("#chat-area", MessageStream).add_system_message(
                f"已选择会话: {self.session_id}"
            )
            self._update_status("idle")

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
            await messages.clear()
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
            if command.arguments[0] not in {
                session.session_id for session in self.client.list_sessions()
            }:
                raise ValueError(f"未知会话: {command.arguments[0]}")
            self.session_id = command.arguments[0]
            self._set_ui_mode("chat")
            await self._show_system_message(f"已选择会话: {self.session_id}")
        elif command.name == "rewind":
            if active:
                raise ValueError("任务运行期间不能回退会话")
            if len(command.arguments) != 1:
                raise ValueError("用法: /rewind 记录ID")
            if self.session_id is None:
                raise ValueError("回退前请先选择会话")
            self.client.rewind_session(self.session_id, command.arguments[0])
            await self._show_system_message(f"已从记录 {command.arguments[0]} 创建分支")
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
            if not active or self.handle is None:
                raise ValueError("仅能在任务运行期间压缩上下文")
            await self.handle.compact()
            await self._show_system_message("已请求压缩上下文")
        elif command.name == "status":
            await self._show_system_message(
                f"会话: {self.session_id or '新会话'}  模型: {self._display_model()}  "
                f"权限: {self.permission_mode}"
            )
        self._update_status("running" if self.handle and not self.handle.done else "idle")

    async def action_cancel_or_quit(self) -> None:
        if self.handle is not None and not self.handle.done:
            await self.handle.cancel()
            return
        self.exit()
