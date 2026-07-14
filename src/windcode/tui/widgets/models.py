from __future__ import annotations

import re
from collections.abc import Mapping
from typing import ClassVar, cast

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from windcode.config import ProviderConfig, ProviderProtocol
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
_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


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


def _field_label(title: str, description: str) -> Static:
    text = Text(title, style="bold")
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
        def __init__(self, alias: str, provider: ProviderConfig, secret: str | None) -> None:
            super().__init__()
            self.alias = alias
            self.provider = provider
            self.secret = secret

    class Delete(Message):
        def __init__(self, alias: str) -> None:
            super().__init__()
            self.alias = alias

    class SetDefault(Message):
        def __init__(self, alias: str) -> None:
            super().__init__()
            self.alias = alias

    class LoadModels(Message):
        def __init__(
            self,
            alias: str,
            provider: ProviderConfig,
            secret: str | None,
        ) -> None:
            super().__init__()
            self.alias = alias
            self.provider = provider
            self.secret = secret

    class Closed(Message):
        pass

    def __init__(
        self,
        profiles: Mapping[str, ProviderConfig],
        *,
        selected: str | None,
        primary: str | None,
        connected: Mapping[str, bool],
        preset_id: str | None = None,
    ) -> None:
        super().__init__(id="provider-manager")
        self.profiles = dict(profiles)
        self.selected = selected if selected in profiles else primary
        self.primary = primary
        self.connected = dict(connected)
        self.initial_preset_id = preset_id
        self._aliases = tuple(self.profiles)
        self._editing_alias: str | None = None
        self._pending_delete: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="provider-dialog"):
            yield Static("Provider 管理", id="provider-manager-title")
            yield OptionList(id="provider-list")
            yield Static("", id="provider-details")
            with Horizontal(id="provider-actions", classes="dialog-actions"):
                yield Button("新增", id="provider-add", variant="primary")
                yield Button("编辑", id="provider-edit")
                yield Button("设为默认", id="provider-default")
                yield Button("断开", id="provider-delete", variant="error")
                yield Button("确认断开", id="provider-confirm-delete", variant="error")
                yield Button("完成", id="provider-close")
            with Vertical(id="provider-editor"):
                with Vertical(id="provider-form"):
                    with Horizontal(classes="provider-field"):
                        yield _field_label("厂商模板", "预填协议、地址和变量")
                        yield Select(
                            (
                                ("自定义 Provider", "custom"),
                                *((preset.name, preset.id) for preset in PROVIDER_PRESETS),
                            ),
                            allow_blank=False,
                            value="openai",
                            id="provider-preset",
                        )
                    with Horizontal(classes="provider-field"):
                        yield _field_label("配置别名", "用于 /model 和配置引用")
                        yield Input(placeholder="Provider 别名", id="provider-alias")
                    with Horizontal(classes="provider-field"):
                        yield _field_label("接口协议", "服务端请求格式")
                        yield Select(
                            tuple(
                                (label, protocol.value)
                                for protocol, label in PROTOCOL_LABELS.items()
                            ),
                            allow_blank=False,
                            value=ProviderProtocol.OPENAI_RESPONSES.value,
                            id="provider-protocol",
                        )
                    with Horizontal(classes="provider-field"):
                        yield _field_label("模型 ID", "可填写或从 Provider 加载")
                        with Horizontal(id="provider-model-controls"):
                            yield Input(placeholder="例如 deepseek-chat", id="provider-model")
                            yield Select[str]((), prompt="选择模型", id="provider-model-options")
                            yield Button("加载", id="provider-load-models")
                    with Horizontal(classes="provider-field"):
                        yield _field_label("Base URL", "API 根地址; 模板已预填")
                        yield Input(
                            placeholder="https://api.example.com/v1", id="provider-base-url"
                        )
                    with Horizontal(classes="provider-field"):
                        yield _field_label("API Key", "存入密钥库; 留空保留")
                        yield Input(placeholder="API Key", password=True, id="provider-api-key")
                    with Horizontal(classes="provider-field"):
                        yield _field_label("环境变量", "变量名; 其值优先于密钥库")
                        yield Input(placeholder="例如 DEEPSEEK_API_KEY", id="provider-api-key-env")
                    yield Static("", id="provider-editor-error")
                    with Center(id="provider-editor-actions-wrap"):
                        with Horizontal(id="provider-editor-actions"):
                            yield Button("保存连接", id="provider-save", variant="primary")
                            yield Button("取消", id="provider-cancel")

    def on_mount(self) -> None:
        self.query_one("#provider-editor", Vertical).display = False
        self.query_one("#provider-confirm-delete", Button).display = False
        self._refresh_options()
        if self._aliases:
            if self.initial_preset_id is not None:
                self._open_editor(None)
            else:
                self.query_one("#provider-list", OptionList).focus()
        else:
            self._open_editor(None)

    def _refresh_options(self) -> None:
        options: list[Option] = []
        for alias in self._aliases:
            provider = self.profiles[alias]
            preset = provider_preset(provider)
            text = Text()
            text.append(alias, style="bold")
            platform = preset.name if preset is not None else PROTOCOL_LABELS[provider.protocol]
            text.append(f"  {provider.model} · {platform}", style="dim")
            text.append(
                "  已连接" if self.connected.get(alias, False) else "  未连接",
                style="green" if self.connected.get(alias, False) else "yellow",
            )
            if alias == self.primary:
                text.append("  默认", style="cyan")
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
            self.query_one("#provider-details", Static).update("连接 Provider 后即可选择模型")
            return
        provider = self.profiles[alias]
        preset = provider_preset(provider)
        env = f"环境变量: {provider.api_key_env}" if provider.api_key_env else "仅使用已保存密钥"
        endpoint = provider.base_url or "官方端点"
        platform = preset.name if preset is not None else "自定义 Provider"
        self.query_one("#provider-details", Static).update(f"{platform} · {env} · {endpoint}")

    def show_error(self, message: str) -> None:
        target = (
            "#provider-editor-error"
            if self.query_one("#provider-editor", Vertical).display
            else "#provider-details"
        )
        self.query_one(target, Static).update(message)

    @on(OptionList.OptionHighlighted, "#provider-list")
    def provider_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._update_details(event.option.id)

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
        elif button_id == "provider-edit" and alias is not None:
            self._open_editor(alias)
        elif button_id == "provider-default" and alias is not None:
            self.post_message(self.SetDefault(alias))
        elif button_id == "provider-delete" and alias is not None:
            self._pending_delete = alias
            event.button.display = False
            self.query_one("#provider-confirm-delete", Button).display = True
            self.query_one("#provider-details", Static).update(
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
        self.query_one("#provider-list", OptionList).display = False
        self.query_one("#provider-details", Static).display = False
        self.query_one("#provider-actions", Horizontal).display = False
        self.query_one("#provider-editor", Vertical).display = True
        alias_input.focus()
        self.call_after_refresh(self._request_models)

    def _apply_preset(self, preset: ProviderPreset) -> None:
        self.query_one("#provider-alias", Input).value = preset.id
        self.query_one("#provider-protocol", Select).value = preset.protocol.value
        self.query_one("#provider-base-url", Input).value = preset.base_url
        self.query_one("#provider-api-key-env", Input).value = preset.api_key_env
        self.query_one("#provider-model", Input).value = ""
        model_options = cast(Select[str], self.query_one("#provider-model-options", Select))
        model_options.set_options(())
        model_options.clear()
        self.call_after_refresh(self._request_models)

    def _request_models(self) -> None:
        alias = self.query_one("#provider-alias", Input).value.strip()
        api_key_env = self.query_one("#provider-api-key-env", Input).value.strip() or None
        secret = self.query_one("#provider-api-key", Input).value.strip() or None
        base_url = self.query_one("#provider-base-url", Input).value.strip() or None
        protocol_value = cast(Select[str], self.query_one("#provider-protocol", Select)).value
        if not _ALIAS_PATTERN.fullmatch(alias) or not isinstance(protocol_value, str):
            return
        preset_value = cast(Select[str], self.query_one("#provider-preset", Select)).value
        try:
            provider = ProviderConfig(
                protocol=ProviderProtocol(protocol_value),
                model=self.query_one("#provider-model", Input).value.strip() or "pending",
                provider_id=(
                    preset_value
                    if isinstance(preset_value, str) and preset_value in PRESETS_BY_ID
                    else None
                ),
                api_key_env=api_key_env,
                credential_id=alias,
                base_url=base_url,
            )
        except (TypeError, ValueError):
            return
        self.query_one("#provider-editor-error", Static).update("正在加载模型列表…")
        self.post_message(self.LoadModels(alias, provider, secret))

    def show_model_ids(self, model_ids: tuple[str, ...]) -> None:
        model_select = cast(Select[str], self.query_one("#provider-model-options", Select))
        model_select.set_options(tuple((model_id, model_id) for model_id in model_ids))
        current = self.query_one("#provider-model", Input).value.strip()
        selected = current if current in model_ids else model_ids[0]
        model_select.value = selected
        self.query_one("#provider-model", Input).value = selected
        self.query_one("#provider-editor-error", Static).update(f"已加载 {len(model_ids)} 个模型")

    def _close_editor(self) -> None:
        self.query_one("#provider-editor", Vertical).display = False
        self.query_one("#provider-list", OptionList).display = True
        self.query_one("#provider-details", Static).display = True
        self.query_one("#provider-actions", Horizontal).display = True
        self.query_one("#provider-list", OptionList).focus()

    def _save_editor(self) -> None:
        alias = self.query_one("#provider-alias", Input).value.strip()
        model = self.query_one("#provider-model", Input).value.strip()
        api_key_env = self.query_one("#provider-api-key-env", Input).value.strip() or None
        secret = self.query_one("#provider-api-key", Input).value.strip() or None
        base_url = self.query_one("#provider-base-url", Input).value.strip() or None
        preset_value = cast(Select[str], self.query_one("#provider-preset", Select)).value
        protocol_value = cast(Select[str], self.query_one("#provider-protocol", Select)).value
        if not _ALIAS_PATTERN.fullmatch(alias):
            self.show_error("别名只能包含字母、数字、点、下划线和连字符")
            return
        if self._editing_alias is None and alias in self.profiles:
            self.show_error(f"Provider 已存在: {alias}")
            return
        if not model:
            self.show_error("请填写模型 ID, 或先加载并选择可用模型")
            return
        if not isinstance(protocol_value, str):
            self.show_error("请选择协议")
            return
        try:
            provider = ProviderConfig(
                protocol=ProviderProtocol(protocol_value),
                model=model,
                provider_id=(
                    preset_value
                    if isinstance(preset_value, str) and preset_value in PRESETS_BY_ID
                    else None
                ),
                api_key_env=api_key_env,
                credential_id=alias,
                base_url=base_url,
            )
        except (ValueError, TypeError) as exc:
            self.show_error(str(exc))
            return
        if provider.protocol is ProviderProtocol.OPENAI_COMPATIBLE and not provider.base_url:
            self.show_error("OpenAI Compatible 协议必须填写 Base URL")
            return
        self.post_message(self.Save(alias, provider, secret))

    def action_close_or_cancel(self) -> None:
        if self.query_one("#provider-editor", Vertical).display:
            self._close_editor()
        else:
            self.post_message(self.Closed())
