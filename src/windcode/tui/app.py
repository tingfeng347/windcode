from __future__ import annotations

import os
from collections import deque
from pathlib import Path
from time import monotonic
from typing import ClassVar, Literal

from rich.text import Text as RichText
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.theme import Theme
from textual.widgets import Static

from windcode.auth import CredentialStore, CredentialStoreError
from windcode.config import AppConfig, PermissionMode, ProviderConfig, default_user_config_path
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
from windcode.memory import MemoryStatus
from windcode.sdk import RunHandle, Windcode
from windcode.tui.commands import (
    COMMANDS,
    CommandDefinition,
    SkillDefinition,
    SlashCommand,
    complete_commands,
    complete_skills,
    parse_command,
)
from windcode.tui.widgets import (
    ApprovalWidget,
    ChatInput,
    CommandMenu,
    ExtensionList,
    MemoryManager,
    MessageStream,
    ModelManager,
    ProviderManager,
    QuestionWidget,
    SessionSelector,
    StatusBar,
    SubagentGroup,
    ToolBlock,
    WelcomeView,
)
from windcode.types import RunRequest


class WindcodeApp(App[None]):
    ESCAPE_INTERRUPT_WINDOW = 1.5
    CSS_PATH = "styles.tcss"
    TITLE = "Windcode"
    INLINE_PADDING = 0
    BINDINGS: ClassVar[list[BindingType]] = [
        ("ctrl+c", "cancel_or_quit", "取消"),
        ("ctrl+q", "quit", "退出"),
        ("ctrl+y", "copy_last_response", "复制回复"),
        Binding("shift+tab", "cycle_permission_mode", "切换权限模式", priority=True),
        Binding("backtab", "cycle_permission_mode", "切换权限模式", priority=True),
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
        config_file: Path | None = None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.workspace = workspace
        self.config_file = (config_file or default_user_config_path()).expanduser().resolve()
        self.model = model
        self.session_id = session_id
        self.permission_mode = permission_mode or config.permission.mode.value
        self.client = Windcode.open(
            config,
            state_root=state_root,
            credential_store=credential_store,
            workspace=workspace,
        )
        self.handle: RunHandle | None = None
        self.tool_blocks: dict[str, ToolBlock] = {}
        self.approval_widgets: dict[str, ApprovalWidget] = {}
        self.session_selector: SessionSelector | None = None
        self.model_manager: ModelManager | None = None
        self.memory_manager: MemoryManager | None = None
        self.provider_manager: ProviderManager | None = None
        self.subagent_group: SubagentGroup | None = None
        self.compact_next_run = False
        self.pending_extension_mutation: tuple[str, str | None] | None = None
        self.prompt_queue: deque[str] = deque()
        self._escape_interrupt_deadline = 0.0
        self.ui_mode: Literal["welcome", "chat"] = "chat"

    def _display_model(self) -> str:
        if self.model:
            provider = self.config.providers.get(self.model)
            return f"{self.model}/{provider.model}" if provider is not None else self.model
        if self.config.primary_provider:
            provider = self.config.providers.get(self.config.primary_provider)
            if provider is not None:
                return provider.model
        return "按配置"

    def _model_setup_message(self) -> str | None:
        if self.client.transport_registry.aliases:
            return None
        if self.config.providers:
            return "模型 Provider 尚未连接, 请检查 API Key 或重新配置 Provider"
        return "尚未配置模型 Provider, 请先连接模型后再开始任务"

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
        with Vertical(id="content-shell"):
            yield WelcomeView(
                model=self._display_model(),
                permission=self.permission_mode,
                sandbox=self.config.sandbox.enabled,
                workspace=self.workspace,
                id="welcome-view",
            )
            yield MessageStream(id="chat-area")
            with Vertical(id="input-dock"):
                with Vertical(id="input-area"):
                    yield CommandMenu(id="command-menu")
                    yield ChatInput(
                        placeholder="输入任务, 或输入 / 使用命令",
                        id="chat-input",
                    )
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
        self._update_status("loading MCP" if self.client.required_mcp_loading else "idle")
        if setup_message := self._model_setup_message():
            self.query_one("#welcome-view", WelcomeView).show_notice(setup_message)
        if self.client.required_mcp_loading:
            self.query_one("#welcome-view", WelcomeView).start_mcp_loading()
            self.run_worker(self._load_required_mcp(), group="mcp-startup", exclusive=True)
        if self.session_id is not None and self.client.session_exists(self.session_id):
            await self._restore_session(self.session_id, announce=False)

    async def _load_required_mcp(self) -> None:
        try:
            await self.client.wait_for_required_mcp()
        except Exception as exc:
            self.query_one("#welcome-view", WelcomeView).stop_mcp_loading()
            self._update_status("MCP 加载失败")
            await self._show_system_message(f"MCP 服务加载失败: {exc}", error=True)
            return
        self.query_one("#welcome-view", WelcomeView).stop_mcp_loading()
        self._update_status("idle")
        await self._show_system_message(self._model_setup_message() or "MCP 服务已加载")

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < 60, "narrow")

    async def on_unmount(self) -> None:
        self.prompt_queue.clear()
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

    async def action_cycle_permission_mode(self) -> None:
        modes = tuple(PermissionMode)
        current = PermissionMode(self.permission_mode)
        self.permission_mode = modes[(modes.index(current) + 1) % len(modes)].value
        active = self.handle is not None and not self.handle.done
        if active:
            assert self.handle is not None
            self.handle.set_permission_mode(self.permission_mode)
        self._update_status("running" if active else "idle")

    async def action_copy_last_response(self) -> None:
        text = self.query_one("#chat-area", MessageStream).last_ai_text
        if not text:
            await self._show_system_message("没有可复制的内容", error=True)
            return
        try:
            self.copy_to_clipboard(text)
            preview = text.replace("\n", " ")[:60]
            await self._show_system_message(
                f"已复制到剪贴板: {preview}{'...' if len(text) > 60 else ''}"
            )
        except Exception:
            await self._show_system_message(
                "复制失败; 终端可能不支持 OSC 52 剪贴板, 试试 Shift+鼠标选择", error=True
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

    def _model_config(
        self,
        providers: dict[str, ProviderConfig],
        *,
        primary: str | None,
        fallback: tuple[str, ...],
    ) -> AppConfig:
        data = self.config.model_dump(mode="python")
        data.update(
            providers=providers,
            primary_provider=primary,
            fallback_chain=fallback,
        )
        return AppConfig.model_validate(data)

    async def _apply_model_config(self, config: AppConfig) -> bool:
        try:
            await self.client.reconfigure_models(config, config_file=self.config_file)
        except (OSError, RuntimeError, ValueError) as exc:
            if self.provider_manager is not None:
                self.provider_manager.show_error(str(exc))
            elif self.model_manager is not None:
                self.model_manager.show_error(str(exc))
            else:
                await self._show_system_message(str(exc), error=True)
            return False
        self.config = config
        return True

    def _connected_providers(self) -> dict[str, bool]:
        connected: dict[str, bool] = {}
        for alias, provider in self.config.providers.items():
            available = bool(provider.api_key_env and os.environ.get(provider.api_key_env))
            if not available and provider.credential_id:
                try:
                    available = bool(self.client.credential_store.get(provider.credential_id))
                except CredentialStoreError:
                    available = False
            connected[alias] = available
        return connected

    async def _open_model_manager(self, *, selected: str | None = None) -> None:
        if self.session_selector is not None:
            await self.session_selector.remove()
            self.session_selector = None
        if self.model_manager is not None:
            await self.model_manager.dismiss()
        self.model_manager = ModelManager(
            self.config.providers,
            selected=selected or self.model,
            primary=self.config.primary_provider,
            connected=self._connected_providers(),
        )
        await self.push_screen(self.model_manager)

    async def _close_model_manager(self) -> None:
        if self.model_manager is not None:
            await self.model_manager.dismiss()
            self.model_manager = None
        self.query_one("#chat-input", ChatInput).focus()

    async def _open_provider_manager(
        self,
        *,
        selected: str | None = None,
        preset_id: str | None = None,
    ) -> None:
        await self._close_model_manager()
        if self.provider_manager is not None:
            await self.provider_manager.dismiss()
        self.provider_manager = ProviderManager(
            self.config.providers,
            selected=selected or self.model,
            primary=self.config.primary_provider,
            connected=self._connected_providers(),
            preset_id=preset_id,
        )
        await self.push_screen(self.provider_manager)

    async def _close_provider_manager(self) -> None:
        if self.provider_manager is not None:
            await self.provider_manager.dismiss()
            self.provider_manager = None
        self.query_one("#chat-input", ChatInput).focus()

    async def _open_memory_manager(self) -> None:
        if self.memory_manager is not None:
            await self.memory_manager.dismiss()
        records = self.client.list_memories() if self.config.memory.enabled else ()
        self.memory_manager = MemoryManager(records, enabled=self.config.memory.enabled)
        await self.push_screen(self.memory_manager)

    async def _close_memory_manager(self) -> None:
        if self.memory_manager is not None:
            await self.memory_manager.dismiss()
            self.memory_manager = None
        self.query_one("#chat-input", ChatInput).focus()

    async def _select_model(self, alias: str) -> None:
        if alias not in self.client.transport_registry.aliases:
            raise ValueError(f"未配置模型: {alias}")
        self.model = alias
        self.query_one("#title-bar", Static).update(self._make_banner())
        self._update_status("idle")
        await self._close_model_manager()
        await self._show_system_message(f"模型: {self._display_model()}")

    @on(ChatInput.SlashMenuUpdate)
    def update_slash_menu(self, event: ChatInput.SlashMenuUpdate) -> None:
        menu = self.query_one("#command-menu", CommandMenu)
        matches = (
            complete_commands(event.prefix, self._extension_command_definitions())
            if event.prefix is not None
            else ()
        )
        menu.show_commands(matches) if matches else menu.hide()

    @on(ChatInput.SkillMenuUpdate)
    def update_skill_menu(self, event: ChatInput.SkillMenuUpdate) -> None:
        menu = self.query_one("#command-menu", CommandMenu)
        skills = tuple(
            SkillDefinition(item.name, item.description) for item in self.client.search_skills()
        )
        matches = complete_skills(event.prefix, skills) if event.prefix is not None else ()
        menu.show_commands(matches) if matches else menu.hide()

    def _extension_command_definitions(self) -> tuple[CommandDefinition, ...]:
        return tuple(
            CommandDefinition(route.name, f"插件命令 · {route.source_id}")
            for route in self.client.extension_commands(reserved=COMMANDS)
        )

    @on(ChatInput.Submitted)
    async def submit_prompt(self, event: ChatInput.Submitted) -> None:
        value = event.text.strip()
        self._hide_command_menu()
        if not value:
            return
        if value.startswith("/"):
            try:
                definitions = self._extension_command_definitions()
                await self._command(
                    parse_command(value, frozenset(item.name for item in definitions))
                )
            except ValueError as exc:
                await self._show_system_message(str(exc), error=True)
            return
        await self._start_prompt(value)

    async def _start_prompt(self, value: str) -> None:
        if setup_message := self._model_setup_message():
            await self._show_system_message(setup_message, error=True)
            await self._open_provider_manager()
            return
        if self.handle is not None and not self.handle.done:
            self.prompt_queue.append(value)
            self._update_status(f"运行中 · 队列 {len(self.prompt_queue)}")
            return
        await self._launch_prompt(value)

    async def _launch_prompt(self, value: str) -> None:
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
            self._escape_interrupt_deadline = 0.0
            self._update_status(result.status)
        finally:
            try:
                self.query_one("#chat-input", ChatInput).focus()
            except NoMatches:
                pass
        if self.prompt_queue:
            value = self.prompt_queue.popleft()
            await self._launch_prompt(value)

    @on(ChatInput.EscapePressed)
    async def escape_pressed(self) -> None:
        if self.handle is None or self.handle.done:
            self._escape_interrupt_deadline = 0.0
            return
        now = monotonic()
        if now <= self._escape_interrupt_deadline:
            self._escape_interrupt_deadline = 0.0
            await self.handle.cancel()
            return
        self._escape_interrupt_deadline = now + self.ESCAPE_INTERRUPT_WINDOW
        self._update_status("再次 Esc 中断")
        self.set_timer(self.ESCAPE_INTERRUPT_WINDOW, self._reset_escape_interrupt)

    def _reset_escape_interrupt(self) -> None:
        if not self._escape_interrupt_deadline or monotonic() < self._escape_interrupt_deadline:
            return
        self._escape_interrupt_deadline = 0.0
        if self.handle is not None and not self.handle.done:
            state = f"运行中 · 队列 {len(self.prompt_queue)}" if self.prompt_queue else "running"
            self._update_status(state)

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

    @on(SessionSelector.Cancelled)
    async def session_cancelled(self) -> None:
        if self.session_selector is not None:
            await self.session_selector.remove()
            self.session_selector = None
        self.query_one("#chat-input", ChatInput).focus()

    @on(ModelManager.Use)
    async def model_use(self, event: ModelManager.Use) -> None:
        await self._select_model(event.alias)

    @on(ModelManager.Manage)
    async def model_manage(self) -> None:
        await self._open_provider_manager()

    @on(ModelManager.Connect)
    async def model_connect(self, event: ModelManager.Connect) -> None:
        await self._open_provider_manager(
            selected=event.alias,
            preset_id=None if event.alias is not None else event.provider_id,
        )

    @on(ProviderManager.Save)
    async def provider_save(self, event: ProviderManager.Save) -> None:
        previous_secret: str | None = None
        if event.secret is not None and event.provider.credential_id is not None:
            try:
                previous_secret = self.client.credential_store.get(event.provider.credential_id)
                self.client.credential_store.set(event.provider.credential_id, event.secret)
            except CredentialStoreError as exc:
                if self.provider_manager is not None:
                    self.provider_manager.show_error(str(exc))
                return
        providers = dict(self.config.providers)
        providers[event.alias] = event.provider
        primary = self.config.primary_provider or event.alias
        config = self._model_config(
            providers,
            primary=primary,
            fallback=self.config.fallback_chain,
        )
        if await self._apply_model_config(config):
            self.model = event.alias
            self.query_one("#title-bar", Static).update(self._make_banner())
            self._update_status("idle")
            await self._open_provider_manager(selected=event.alias)
        elif event.secret is not None and event.provider.credential_id is not None:
            if previous_secret is None:
                self.client.credential_store.delete(event.provider.credential_id)
            else:
                self.client.credential_store.set(event.provider.credential_id, previous_secret)

    @on(ProviderManager.Delete)
    async def provider_delete(self, event: ProviderManager.Delete) -> None:
        provider = self.config.providers.get(event.alias)
        providers = dict(self.config.providers)
        providers.pop(event.alias, None)
        primary = self.config.primary_provider
        if primary == event.alias:
            primary = next(iter(providers), None)
        fallback = tuple(
            alias
            for alias in self.config.fallback_chain
            if alias != event.alias and alias != primary and alias in providers
        )
        config = self._model_config(providers, primary=primary, fallback=fallback)
        if await self._apply_model_config(config):
            if self.model == event.alias:
                self.model = primary
            self.query_one("#title-bar", Static).update(self._make_banner())
            self._update_status("idle")
            if provider is not None and provider.credential_id is not None:
                try:
                    self.client.credential_store.delete(provider.credential_id)
                except CredentialStoreError as exc:
                    await self._show_system_message(str(exc), error=True)
            await self._open_provider_manager(selected=self.model)

    @on(ProviderManager.SetDefault)
    async def provider_set_default(self, event: ProviderManager.SetDefault) -> None:
        ordered = (
            self.config.primary_provider,
            *self.config.fallback_chain,
        )
        fallback = tuple(
            alias
            for alias in ordered
            if alias is not None and alias != event.alias and alias in self.config.providers
        )
        config = self._model_config(
            dict(self.config.providers),
            primary=event.alias,
            fallback=fallback,
        )
        if await self._apply_model_config(config):
            self.model = event.alias
            self.query_one("#title-bar", Static).update(self._make_banner())
            self._update_status("idle")
            await self._open_provider_manager(selected=event.alias)

    @on(ModelManager.Closed)
    async def model_manager_closed(self) -> None:
        await self._close_model_manager()

    @on(ProviderManager.Closed)
    async def provider_manager_closed(self) -> None:
        await self._close_provider_manager()
        await self._open_model_manager()

    @on(MemoryManager.EnabledChanged)
    async def memory_enabled_changed(self, event: MemoryManager.EnabledChanged) -> None:
        try:
            self.client.set_memory_enabled(event.enabled, config_file=self.config_file)
        except (OSError, ValueError) as exc:
            await self._show_system_message(f"无法保存长期记忆设置: {exc}", error=True)
            return
        self.config = self.client.config
        if self.memory_manager is not None:
            records = self.client.list_memories() if event.enabled else ()
            self.memory_manager.refresh_records(records)

    @on(MemoryManager.Forget)
    async def memory_forget(self, event: MemoryManager.Forget) -> None:
        self.client.delete_memory(event.memory_id)
        if self.memory_manager is not None:
            self.memory_manager.refresh_records(self.client.list_memories())

    @on(MemoryManager.ActivationChanged)
    async def memory_activation_changed(self, event: MemoryManager.ActivationChanged) -> None:
        self.client.set_memory_activation(event.memory_id, event.activation)
        if self.memory_manager is not None:
            self.memory_manager.refresh_records(self.client.list_memories())

    @on(MemoryManager.Rebuild)
    async def memory_rebuild(self) -> None:
        self.client.rebuild_memory_index()
        if self.memory_manager is not None:
            self.memory_manager.refresh_records(self.client.list_memories())

    @on(MemoryManager.Closed)
    async def memory_manager_closed(self) -> None:
        await self._close_memory_manager()

    async def _command(self, command: SlashCommand) -> None:
        messages = self.query_one("#chat-area", MessageStream)
        active = self.handle is not None and not self.handle.done
        routes = {route.name: route for route in self.client.extension_commands(reserved=COMMANDS)}
        route = routes.get(command.name)
        if route is not None:
            target_kind, selector_value = route.target.split(":", 1)
            selector = {
                "skill": f"${selector_value}",
                "prompt": f"@prompt:{selector_value}",
                "capability": f"@capability:{selector_value}",
            }[target_kind]
            prompt = " ".join((selector, *command.arguments))
            await self._start_prompt(prompt)
            return
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
            if len(command.arguments) != 1:
                raise ValueError("用法: /mode 模式")
            try:
                self.permission_mode = PermissionMode(command.arguments[0]).value
            except ValueError as exc:
                raise ValueError(f"未知权限模式: {command.arguments[0]}") from exc
            if active:
                assert self.handle is not None
                self.handle.set_permission_mode(self.permission_mode)
            self._update_status("running" if active else "idle")
        elif command.name == "model":
            if active:
                raise ValueError("任务运行期间不能切换模型")
            if not command.arguments:
                await self._open_model_manager()
                return
            if len(command.arguments) != 1:
                raise ValueError("用法: /model [配置别名]")
            await self._select_model(command.arguments[0])
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
            for definition in complete_commands("/", self._extension_command_definitions()):
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
        elif command.name == "memory":
            if active:
                raise ValueError("任务运行期间不能管理长期记忆")
            if not command.arguments:
                await self._open_memory_manager()
                return
            action = command.arguments[0] if command.arguments else "status"
            arguments = command.arguments[1:]
            if not self.config.memory.enabled:
                if action == "status":
                    await self._show_system_message("长期记忆: 已禁用")
                    return
                raise ValueError("长期记忆已在配置中禁用")
            if action == "status":
                records = self.client.list_memories()
                candidates = sum(item.status is MemoryStatus.CANDIDATE for item in records)
                active_count = sum(item.status is MemoryStatus.ACTIVE for item in records)
                await self._show_system_message(
                    f"长期记忆: 已启用; 生效 {active_count}; 候选 {candidates}; 总计 {len(records)}"
                )
            elif action == "candidates":
                records = self.client.list_memories(status=MemoryStatus.CANDIDATE)
                text = "\n".join(f"{item.memory_id[:10]}  {item.title}" for item in records)
                await self._show_system_message(text or "没有待确认的记忆候选")
            elif action == "search":
                if not arguments:
                    raise ValueError("用法: /memory search 关键词")
                records = self.client.search_memories(" ".join(arguments))
                text = "\n".join(
                    f"{item.memory_id[:10]}  [{item.status.value}] {item.title}" for item in records
                )
                await self._show_system_message(text or "没有匹配的记忆")
            elif action == "show":
                if len(arguments) != 1:
                    raise ValueError("用法: /memory show ID")
                matches = tuple(
                    item
                    for item in self.client.list_memories()
                    if item.memory_id.startswith(arguments[0])
                )
                if len(matches) != 1:
                    raise ValueError("记忆 ID 不存在或前缀不唯一")
                item = matches[0]
                await self._show_system_message(
                    f"{item.title}\n类型: {item.kind.value}; 范围: {item.scope.value}; "
                    f"状态: {item.status.value}; 激活: {item.activation.value}; "
                    f"优先级: {item.priority}\n摘要: {item.summary}\n\n{item.body}"
                )
            elif action == "activation":
                if len(arguments) != 2:
                    raise ValueError("用法: /memory activation ID <always|search|manual>")
                matches = tuple(
                    item
                    for item in self.client.list_memories()
                    if item.memory_id.startswith(arguments[0])
                )
                if len(matches) != 1:
                    raise ValueError("记忆 ID 不存在或前缀不唯一")
                if matches[0].status is not MemoryStatus.ACTIVE:
                    raise ValueError("候选或非生效记忆必须先确认, 才能设置自动激活策略")
                updated = self.client.set_memory_activation(matches[0].memory_id, arguments[1])
                await self._show_system_message(
                    f"记忆激活策略已更新: {updated.title} -> {updated.activation.value}"
                )
            elif action in {"confirm", "reject", "forget"}:
                if len(arguments) != 1:
                    raise ValueError(f"用法: /memory {action} ID")
                matches = tuple(
                    item
                    for item in self.client.list_memories()
                    if item.memory_id.startswith(arguments[0])
                )
                if len(matches) != 1:
                    raise ValueError("记忆 ID 不存在或前缀不唯一")
                item = matches[0]
                if action == "confirm":
                    updated = self.client.confirm_memory(item.memory_id)
                    await self._show_system_message(f"记忆已确认: {updated.title}")
                elif action == "reject":
                    updated = self.client.reject_memory(item.memory_id)
                    await self._show_system_message(f"记忆已拒绝: {updated.title}")
                else:
                    self.client.delete_memory(item.memory_id)
                    await self._show_system_message(f"记忆已删除: {item.title}")
            elif action == "rebuild":
                count = self.client.rebuild_memory_index()
                await self._show_system_message(f"记忆索引已重建: {count} 条")
            else:
                raise ValueError(
                    "用法: /memory [status|candidates|search|show|activation|confirm|reject|"
                    "forget|rebuild]"
                )
        elif command.name == "extensions":
            action = command.arguments[0] if command.arguments else "list"
            target = command.arguments[1] if len(command.arguments) > 1 else None
            if len(command.arguments) > 2:
                raise ValueError("用法: /extensions [操作] [目标]")
            mutations = {"install", "enable", "disable", "reload", "trust"}
            if active and action in mutations:
                raise ValueError("任务运行期间不能修改扩展状态")
            mutation = (action, target)
            if action in mutations and self.pending_extension_mutation != mutation:
                self.pending_extension_mutation = mutation
                target_label = "" if target is None else f" {target}"
                await self._show_system_message(
                    f"确认扩展操作: {action}{target_label}; 再次输入相同命令执行"
                )
                return
            if action in mutations:
                self.pending_extension_mutation = None
            if action == "list":
                records = await self.client.list_extensions()
            elif action == "inspect":
                if target is None:
                    raise ValueError("用法: /extensions inspect 目标")
                records = await self.client.inspect_extension(target)
            elif action == "install":
                if target is None:
                    raise ValueError("用法: /extensions install 路径")
                result = await self.client.install_extension(
                    Path(target).expanduser()  # noqa: ASYNC240 - local command parsing
                )
                await self._show_system_message(
                    f"已安装 {result.manifest.plugin_id}, 默认禁用; "
                    "运行 /extensions reload 后刷新目录"
                )
                records = await self.client.list_extensions()
            elif action in {"enable", "disable"}:
                if target is None:
                    raise ValueError(f"用法: /extensions {action} 目标")
                await self.client.set_extension_enabled(target, action == "enable")
                await self._show_system_message("扩展状态已更新; 显式 reload 后影响新运行")
                records = await self.client.list_extensions()
            elif action == "reload":
                await self.client.reload_extensions()
                records = await self.client.list_extensions()
            elif action == "trust":
                trust_path = (
                    self.workspace if target is None else Path(target).expanduser()  # noqa: ASYNC240 - local command parsing
                )
                await self.client.trust_extension_workspace(trust_path)
                await self._show_system_message("工作区信任已记录; 显式 reload 后生效")
                records = await self.client.list_extensions()
            else:
                raise ValueError(f"未知扩展操作: {action}")
            await self._show_system_message(ExtensionList.render_text(records))
        self._update_status("running" if self.handle and not self.handle.done else "idle")

    async def action_cancel_or_quit(self) -> None:
        # If the user has selected text, copy it first instead of canceling.
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            self.screen.clear_selection()
            return
        if self.handle is not None and not self.handle.done:
            await self.handle.cancel()
            return
        self.exit()
