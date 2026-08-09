from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, cast

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from windcode.config import ProviderConfig, ProviderProtocol
from windcode.providers import ProviderDraft, ProviderHealth, ProviderStatus
from windcode.providers.catalog import (
    PRESETS_BY_ID,
    PROVIDER_PRESETS,
    ProviderPreset,
    provider_preset,
)

PROTOCOL_LABELS = {
    ProviderProtocol.ANTHROPIC_MESSAGES: "Anthropic",
    ProviderProtocol.OPENAI_RESPONSES: "OpenAI Responses",
    ProviderProtocol.OPENAI_COMPATIBLE: "OpenAI Compatible",
}
DEFAULT_KEY_ENV = {
    ProviderProtocol.ANTHROPIC_MESSAGES: "ANTHROPIC_API_KEY",
    ProviderProtocol.OPENAI_RESPONSES: "OPENAI_API_KEY",
    ProviderProtocol.OPENAI_COMPATIBLE: "OPENAI_API_KEY",
}


def _model_option(
    alias: str,
    provider: ProviderConfig,
    *,
    selected: str | None,
    primary: str | None,
    connected: bool,
) -> Option:
    text = Text()
    text.append("● " if alias == selected else "  ", style="cyan")
    text.append(provider.model, style="bold")
    text.append(f"  {alias}", style="dim")
    if alias == selected:
        text.append("  当前", style="bold cyan")
    elif alias == primary:
        text.append("  默认", style="cyan")
    if not connected:
        text.append("  未连接", style="yellow")
    return Option(text, id=alias)


def _field_label(title: str, description: str = "") -> Static:
    text = Text(title, style="bold")
    if description:
        text.append(f"\n{description}", style="dim")
    return Static(text, classes="provider-field-label")


class ModelManager(ModalScreen[None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "关闭"),
        Binding("left", "previous_provider", "上一个厂商", priority=True),
        Binding("right", "next_provider", "下一个厂商", priority=True),
        Binding("up", "cursor_up", "上一个模型", priority=True),
        Binding("down", "cursor_down", "下一个模型", priority=True),
    ]

    class Use(Message):
        def __init__(self, alias: str) -> None:
            super().__init__()
            self.alias = alias

    class Manage(Message):
        pass

    class Connect(Message):
        def __init__(self, provider_id: str, alias: str | None = None) -> None:
            super().__init__()
            self.provider_id = provider_id
            self.alias = alias

    class Closed(Message):
        pass

    def __init__(
        self,
        profiles: Mapping[str, ProviderConfig],
        *,
        selected: str | None,
        primary: str | None,
        connected: Mapping[str, bool],
    ) -> None:
        super().__init__(id="model-manager")
        self.profiles = dict(profiles)
        self.selected = selected if selected in profiles else primary
        self.primary = primary
        self.connected = dict(connected)
        self._visible_aliases: tuple[str, ...] = ()
        groups: list[tuple[str | None, str]] = [
            (None, "全部"),
            *((preset.id, preset.name) for preset in PROVIDER_PRESETS),
        ]
        seen = {preset.id for preset in PROVIDER_PRESETS}
        for alias, provider in self.profiles.items():
            preset = provider_preset(provider)
            group_id = preset.id if preset is not None else alias
            if group_id not in seen:
                seen.add(group_id)
                groups.append((group_id, preset.name if preset is not None else alias))
        self._groups = tuple(groups)
        self._group_index = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="model-dialog"):
            yield Static("选择模型", id="model-manager-title")
            yield Input(placeholder="搜索模型或 Provider", id="model-search")
            yield Static("", id="model-provider-tabs")
            yield OptionList(id="model-list")
            yield Static("", id="model-picker-status")
            with Horizontal(classes="dialog-actions"):
                yield Button("使用", id="model-use", variant="primary")
                yield Button("管理 Provider", id="model-manage")
                yield Button("关闭", id="model-close")

    def on_mount(self) -> None:
        self._filter("")
        self.query_one("#model-search", Input).focus()

    def _filter(self, query: str) -> None:
        query = query.casefold().strip()
        active_group = self._groups[self._group_index][0]
        matched: list[str] = []
        for alias, provider in self.profiles.items():
            preset = provider_preset(provider)
            group_id = preset.id if preset is not None else alias
            if active_group is not None and group_id != active_group:
                continue
            if query and not any(
                query in candidate.casefold()
                for candidate in (
                    alias,
                    provider.model,
                    PROTOCOL_LABELS[provider.protocol],
                    preset.name if preset is not None else "",
                )
            ):
                continue
            matched.append(alias)
        aliases = tuple(matched)
        self._visible_aliases = aliases
        options: list[Option] = []
        last_group: str | None = None
        for alias in aliases:
            provider = self.profiles[alias]
            preset = provider_preset(provider)
            group_name = preset.name if preset is not None else alias
            if not query and group_name != last_group:
                options.append(Option(Text(group_name, style="bold magenta"), disabled=True))
                last_group = group_name
            options.append(
                _model_option(
                    alias,
                    provider,
                    selected=self.selected,
                    primary=self.primary,
                    connected=self.connected.get(alias, False),
                )
            )
        configured_presets = {
            preset.id
            for provider in self.profiles.values()
            if (preset := provider_preset(provider)) is not None
        }
        available_presets = tuple(
            preset
            for preset in PROVIDER_PRESETS
            if preset.id not in configured_presets
            and (active_group is None or preset.id == active_group)
            and (not query or query in preset.name.casefold() or query in preset.id.casefold())
        )
        if available_presets:
            if active_group is None:
                options.append(Option(Text("连接 Provider", style="bold magenta"), disabled=True))
            for preset in available_presets:
                text = Text("  ")
                text.append(preset.name, style="bold")
                text.append("  尚未连接", style="dim")
                options.append(Option(text, id=f"connect:{preset.id}"))
        model_list = self.query_one("#model-list", OptionList)
        model_list.clear_options()
        model_list.add_options(options)
        status = "" if options else "没有匹配的模型或 Provider"
        self.query_one("#model-picker-status", Static).update(status)
        target = self.selected if self.selected in aliases else None
        if target is not None:
            model_list.highlighted = model_list.get_option_index(target)
        elif options:
            model_list.action_first()
        self._update_group_tabs()

    def _update_group_tabs(self) -> None:
        tabs = Text()
        previous = self._groups[(self._group_index - 1) % len(self._groups)][1]
        current = self._groups[self._group_index][1]
        following = self._groups[(self._group_index + 1) % len(self._groups)][1]
        tabs.append(f"← {previous}", style="dim")
        tabs.append(f"    {current}    ", style="bold cyan")
        tabs.append(f"{following} →", style="dim")
        self.query_one("#model-provider-tabs", Static).update(tabs)

    def _highlighted_alias(self) -> str | None:
        model_list = self.query_one("#model-list", OptionList)
        option = model_list.highlighted_option
        return option.id if option is not None else None

    def action_previous_provider(self) -> None:
        self._group_index = (self._group_index - 1) % len(self._groups)
        self._filter(self.query_one("#model-search", Input).value)

    def action_next_provider(self) -> None:
        self._group_index = (self._group_index + 1) % len(self._groups)
        self._filter(self.query_one("#model-search", Input).value)

    def action_cursor_up(self) -> None:
        self.query_one("#model-list", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#model-list", OptionList).action_cursor_down()

    def _use(self, alias: str | None = None) -> None:
        target = alias or self._highlighted_alias()
        if target is None:
            return
        if target.startswith("connect:"):
            self.post_message(self.Connect(target.removeprefix("connect:")))
            return
        if not self.connected.get(target, False):
            preset = provider_preset(self.profiles[target])
            if preset is not None:
                self.post_message(self.Connect(preset.id, target))
            else:
                self.query_one("#model-picker-status", Static).update("该 Provider 尚未连接")
            return
        self.post_message(self.Use(target))

    def show_error(self, message: str) -> None:
        self.query_one("#model-picker-status", Static).update(message)

    @on(Input.Changed, "#model-search")
    def search_changed(self, event: Input.Changed) -> None:
        self._filter(event.value)

    @on(Input.Submitted, "#model-search")
    def search_submitted(self) -> None:
        self._use()

    @on(OptionList.OptionSelected, "#model-list")
    def option_selected(self, event: OptionList.OptionSelected) -> None:
        self._use(event.option.id)

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "model-use":
            self._use()
        elif event.button.id == "model-manage":
            self.post_message(self.Manage())
        elif event.button.id == "model-close":
            self.action_close()

    def action_close(self) -> None:
        self.post_message(self.Closed())


class ProviderManager(ModalScreen[None]):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "close_or_cancel", "返回")]

    class Save(Message):
        def __init__(self, draft: ProviderDraft) -> None:
            super().__init__()
            self.draft = draft

    class Delete(Message):
        def __init__(self, alias: str) -> None:
            super().__init__()
            self.alias = alias

    class SetDefault(Message):
        def __init__(self, alias: str) -> None:
            super().__init__()
            self.alias = alias

    class LoadModels(Message):
        def __init__(self, draft: ProviderDraft) -> None:
            super().__init__()
            self.draft = draft

    class Closed(Message):
        pass

    def __init__(
        self,
        health: tuple[ProviderHealth, ...],
        *,
        selected: str | None,
        preset_id: str | None = None,
    ) -> None:
        super().__init__(id="provider-manager")
        self.health = {item.alias: item for item in health}
        self.profiles = {item.alias: item.provider for item in health}
        primary = next((item.alias for item in health if item.is_default), None)
        self.selected = selected if selected in self.profiles else primary
        self.initial_preset_id = preset_id
        self._aliases = tuple(self.profiles)
        self._editing_alias: str | None = None
        self._pending_delete: str | None = None
        self._draft_health: ProviderHealth | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="provider-dialog"):
            with Horizontal(id="provider-manager-header"):
                with Vertical(id="provider-heading"):
                    yield Static("Providers", id="provider-manager-title")
                    yield Static("模型连接与默认配置", id="provider-manager-subtitle")
                yield Button("关闭", id="provider-close", flat=True)
            with Horizontal(id="provider-workspace"):
                with Vertical(id="provider-sidebar"):
                    yield Static("连接", classes="provider-panel-title")
                    yield OptionList(id="provider-list")
                    with Horizontal(id="provider-sidebar-actions"):
                        yield Button("新增", id="provider-add", variant="primary", flat=True)
                        yield Button("设为默认", id="provider-default", flat=True)
                with Vertical(id="provider-editor"):
                    with Horizontal(id="provider-editor-header"):
                        yield Static("连接配置", classes="provider-panel-title")
                        yield Static("保存后生效", id="provider-editor-hint")
                    with Vertical(id="provider-inspector"):
                        yield Static("", id="provider-details")
                    with VerticalScroll(id="provider-form-scroll"):
                        with Vertical(id="provider-form"):
                            with Vertical(classes="provider-field"):
                                yield _field_label("厂商模板")
                                yield Select(
                                    (
                                        ("自定义 Provider", "custom"),
                                        *((preset.name, preset.id) for preset in PROVIDER_PRESETS),
                                    ),
                                    allow_blank=False,
                                    value="openai",
                                    id="provider-preset",
                                )
                            with Vertical(classes="provider-field"):
                                yield _field_label("API Key")
                                yield Input(
                                    placeholder="API Key", password=True, id="provider-api-key"
                                )
                            with Vertical(classes="provider-field"):
                                yield _field_label("模型 ID")
                                with Horizontal(id="provider-model-controls"):
                                    yield Input(
                                        placeholder="例如 deepseek-chat", id="provider-model"
                                    )
                                    yield Button("加载", id="provider-load-models")
                                yield Select[str](
                                    (),
                                    prompt="从已加载模型中选择",
                                    id="provider-model-options",
                                )
                            with Vertical(classes="provider-field"):
                                yield _field_label("配置别名")
                                yield Input(placeholder="Provider 别名", id="provider-alias")
                            with Vertical(classes="provider-field"):
                                yield _field_label("接口协议")
                                yield Select(
                                    tuple(
                                        (label, protocol.value)
                                        for protocol, label in PROTOCOL_LABELS.items()
                                    ),
                                    allow_blank=False,
                                    value=ProviderProtocol.OPENAI_RESPONSES.value,
                                    id="provider-protocol",
                                )
                            with Vertical(classes="provider-field"):
                                yield _field_label("Base URL")
                                yield Input(
                                    placeholder="https://api.example.com/v1", id="provider-base-url"
                                )
                            with Vertical(classes="provider-field"):
                                yield _field_label("环境变量")
                                yield Input(
                                    placeholder="例如 DEEPSEEK_API_KEY", id="provider-api-key-env"
                                )
                    yield Static("", id="provider-editor-error")
                    with Horizontal(id="provider-editor-actions"):
                        yield Button("保存更改", id="provider-save", variant="primary", flat=True)
                        yield Button("取消", id="provider-cancel", flat=True)
                        yield Button("断开连接", id="provider-delete", variant="error", flat=True)
                        yield Button(
                            "确认断开",
                            id="provider-confirm-delete",
                            variant="error",
                            flat=True,
                        )

    def on_mount(self) -> None:
        self.query_one("#provider-confirm-delete", Button).display = False
        self._refresh_options()
        self._open_editor(self.selected if self.initial_preset_id is None else None)

    def _refresh_options(self) -> None:
        options: list[Option] = []
        for alias in self._aliases:
            provider = self.profiles[alias]
            text = Text()
            health = self.health[alias]
            text.append(
                "● ",
                style=(
                    "#9ece6a"
                    if health.status is ProviderStatus.READY
                    else "#f7768e"
                    if health.status is ProviderStatus.ERROR
                    else "#e0af68"
                ),
            )
            text.append(alias, style="bold")
            text.append(f"\n  {provider.model}", style="dim")
            if health.is_default:
                text.append(" 默认", style="cyan")
            options.append(Option(text, id=alias))
        provider_list = self.query_one("#provider-list", OptionList)
        provider_list.clear_options()
        provider_list.add_options(options)
        if self.selected in self._aliases:
            provider_list.highlighted = self._aliases.index(self.selected)
        self._update_details(self._highlighted_alias())

    def _highlighted_alias(self) -> str | None:
        index = self.query_one("#provider-list", OptionList).highlighted
        return self._aliases[index] if index is not None and index < len(self._aliases) else None

    def _update_details(self, alias: str | None) -> None:
        if alias is None:
            if self._draft_health is not None:
                health = self._draft_health
                self.query_one("#provider-details", Static).update(self._health_summary(health))
                return
            details = Text()
            details.append("○ 新建连接", style="bold cyan")
            details.append("  选择厂商模板并填写模型与凭据", style="dim")
            self.query_one("#provider-details", Static).update(details)
            return
        health = self.health[alias]
        self.query_one("#provider-details", Static).update(self._health_summary(health))

    def _health_summary(self, health: ProviderHealth) -> Text:
        provider = health.provider
        preset = provider_preset(provider)
        platform = preset.name if preset is not None else "自定义 Provider"
        connection = {
            ProviderStatus.READY: "已连接",
            ProviderStatus.DISCONNECTED: "未连接",
            ProviderStatus.ERROR: "错误",
        }[health.status]
        status_style = {
            ProviderStatus.READY: "#9ece6a",
            ProviderStatus.DISCONNECTED: "#e0af68",
            ProviderStatus.ERROR: "#f7768e",
        }[health.status]
        details = Text()
        details.append("● ", style=status_style)
        details.append(connection, style="bold")
        if health.is_default:
            details.append(" · 默认", style="bold cyan")
        details.append(f"  {platform} · {PROTOCOL_LABELS[provider.protocol]}", style="dim")
        details.append("\n")
        details.append(health.alias, style="bold")
        details.append(f" · {provider.model}", style="dim")
        credential = provider.api_key_env or "密钥库"
        credential_status = "已设置" if health.credential_source else "未设置"
        details.append(
            f"\n{credential} · {credential_status} · {health.loaded_model_count} 个模型",
            style="dim",
        )
        if health.diagnostic:
            details.append(f"\n{health.diagnostic}", style="red")
        return details

    def show_error(self, message: str) -> None:
        self.query_one("#provider-editor-error", Static).update(message)

    @on(OptionList.OptionHighlighted, "#provider-list")
    def provider_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._open_editor(event.option.id)

    @on(Select.Changed, "#provider-preset")
    def preset_changed(self, event: Select.Changed) -> None:
        if self._editing_alias is not None or not isinstance(event.value, str):
            return
        preset = PRESETS_BY_ID.get(event.value)
        if preset is not None:
            self._apply_preset(preset)

    @on(Select.Changed, "#provider-model-options")
    def model_option_changed(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            self.query_one("#provider-model", Input).value = event.value

    @on(Input.Submitted, "#provider-api-key")
    def api_key_submitted(self) -> None:
        self._request_models()

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        alias = self._highlighted_alias()
        if button_id == "provider-add":
            self._open_editor(None)
        elif button_id == "provider-default" and alias is not None:
            self.post_message(self.SetDefault(alias))
        elif button_id == "provider-delete" and alias is not None:
            self._pending_delete = alias
            event.button.display = False
            self.query_one("#provider-confirm-delete", Button).display = True
            self.query_one("#provider-editor-error", Static).update(
                f"再次确认将断开 {alias} 并删除已保存密钥"
            )
        elif button_id == "provider-confirm-delete" and self._pending_delete is not None:
            self.post_message(self.Delete(self._pending_delete))
        elif button_id == "provider-close":
            self.post_message(self.Closed())
        elif button_id == "provider-save":
            self._save_editor()
        elif button_id == "provider-load-models":
            self._request_models()
        elif button_id == "provider-cancel":
            self._close_editor()

    def _open_editor(self, alias: str | None) -> None:
        self._editing_alias = alias
        self._pending_delete = None
        self._draft_health = None
        self.query_one("#provider-delete", Button).display = alias is not None
        self.query_one("#provider-confirm-delete", Button).display = False
        provider = self.profiles.get(alias) if alias else None
        existing_preset = provider_preset(provider) if provider is not None else None
        new_preset = PRESETS_BY_ID.get(self.initial_preset_id or "openai")
        new_preset = new_preset or PRESETS_BY_ID["openai"]
        preset_select = cast(Select[str], self.query_one("#provider-preset", Select))
        preset_select.value = (
            existing_preset.id
            if existing_preset is not None
            else ("custom" if provider is not None else new_preset.id)
        )
        preset_select.disabled = provider is not None
        alias_input = self.query_one("#provider-alias", Input)
        alias_input.value = alias or new_preset.id
        alias_input.disabled = provider is not None
        protocol = provider.protocol if provider else new_preset.protocol
        self.query_one("#provider-protocol", Select).value = protocol.value
        self.query_one("#provider-model", Input).value = provider.model if provider else ""
        self.query_one("#provider-base-url", Input).value = (
            provider.base_url or "" if provider else new_preset.base_url
        )
        self.query_one("#provider-api-key", Input).value = ""
        self.query_one("#provider-api-key-env", Input).value = (
            provider.api_key_env or DEFAULT_KEY_ENV[protocol]
            if provider
            else new_preset.api_key_env
        )
        self.query_one("#provider-api-key", Input).placeholder = (
            "新 API Key (留空则保留)" if provider else "API Key (也可只使用环境变量)"
        )
        self.query_one("#provider-editor-error", Static).update("")
        model_options = cast(Select[str], self.query_one("#provider-model-options", Select))
        model_options.set_options(())
        model_options.clear()
        self._update_details(alias)
        self.query_one("#provider-api-key", Input).focus()

    def _apply_preset(self, preset: ProviderPreset) -> None:
        self.query_one("#provider-alias", Input).value = preset.id
        self.query_one("#provider-protocol", Select).value = preset.protocol.value
        self.query_one("#provider-base-url", Input).value = preset.base_url
        self.query_one("#provider-api-key-env", Input).value = preset.api_key_env
        self.query_one("#provider-model", Input).value = ""
        model_options = cast(Select[str], self.query_one("#provider-model-options", Select))
        model_options.set_options(())
        model_options.clear()
        self._update_details(None)

    def _draft(self) -> ProviderDraft | None:
        protocol_value = cast(Select[str], self.query_one("#provider-protocol", Select)).value
        if not isinstance(protocol_value, str):
            return None
        preset_value = cast(Select[str], self.query_one("#provider-preset", Select)).value
        alias = self.query_one("#provider-alias", Input).value.strip()
        return ProviderDraft(
            alias=alias,
            protocol=ProviderProtocol(protocol_value),
            model=self.query_one("#provider-model", Input).value.strip(),
            provider_id=(
                preset_value
                if isinstance(preset_value, str) and preset_value in PRESETS_BY_ID
                else None
            ),
            api_key_env=self.query_one("#provider-api-key-env", Input).value.strip() or None,
            credential_id=alias or None,
            base_url=self.query_one("#provider-base-url", Input).value.strip() or None,
            secret=self.query_one("#provider-api-key", Input).value.strip() or None,
            editing_alias=self._editing_alias,
        )

    def _request_models(self) -> None:
        draft = self._draft()
        if draft is None:
            return
        self.query_one("#provider-editor-error", Static).update("正在加载模型列表…")
        self.post_message(self.LoadModels(draft))

    def update_health(self, health: ProviderHealth) -> None:
        self.health[health.alias] = health
        if health.alias not in self.profiles:
            self._draft_health = health

    def show_model_ids(self, model_ids: tuple[str, ...]) -> None:
        model_select = cast(Select[str], self.query_one("#provider-model-options", Select))
        model_select.set_options(tuple((model_id, model_id) for model_id in model_ids))
        current = self.query_one("#provider-model", Input).value.strip()
        selected = current if current in model_ids else model_ids[0]
        model_select.value = selected
        self.query_one("#provider-model", Input).value = selected
        self.query_one("#provider-editor-error", Static).update(f"已加载 {len(model_ids)} 个模型")
        self._update_details(self._editing_alias)

    def _close_editor(self) -> None:
        self._open_editor(self._highlighted_alias())

    def _save_editor(self) -> None:
        draft = self._draft()
        if draft is not None:
            self.post_message(self.Save(draft))

    def action_close_or_cancel(self) -> None:
        self._close_editor()
