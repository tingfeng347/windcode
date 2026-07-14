from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from windcode.extensions.models import CapabilityKind, CapabilityRecord


class ExtensionManager(ModalScreen[None]):
    """Keyboard-first extension manager grouped by capability type."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "关闭"),
        Binding("space", "toggle_extension", "启用/禁用", show=True),
        Binding("enter", "inspect", "查看状态", show=True),
    ]

    class Toggle(Message):
        def __init__(self, identifier: str, enabled: bool) -> None:
            super().__init__()
            self.identifier = identifier
            self.enabled = enabled

    class Closed(Message):
        pass

    def __init__(self, records: tuple[CapabilityRecord, ...]) -> None:
        super().__init__()
        self.records = records

    def compose(self) -> ComposeResult:
        with Vertical(id="extension-dialog"):
            with Horizontal(id="extension-manager-header"):
                yield Static("扩展", id="extension-manager-title")
                yield Static("Esc 关闭", id="extension-manager-close")
            yield Static("方向键选择 · Enter 查看状态 · Space 启用或禁用", id="extension-help")
            yield OptionList(*self._options(), id="extension-list")
            yield Static("选择扩展查看状态", id="extension-details")

    def on_mount(self) -> None:
        self.query_one("#extension-list", OptionList).focus()

    def _options(self) -> tuple[Option, ...]:
        options: list[Option] = []
        groups = (
            ("技能", CapabilityKind.SKILL, "$"),
            ("MCP", CapabilityKind.MCP_SERVER, ""),
            ("插件", CapabilityKind.PLUGIN, ""),
        )
        for label, kind, prefix in groups:
            records = sorted(
                (record for record in self.records if record.kind is kind),
                key=lambda record: (record.public_name, record.capability_id),
            )
            if not records:
                continue
            options.append(
                Option(Text(label, style="bold #aab3ba"), id=f"group:{kind.value}", disabled=True)
            )
            for record in records:
                text = Text("  ")
                text.append(prefix + record.public_name, style="bold" if record.enabled else "dim")
                text.append("  ")
                text.append(
                    "✓ 已启用" if record.enabled else "○ 已禁用",
                    style=("#70b892" if record.enabled else "#88939b"),
                )
                options.append(Option(text, id=record.capability_id))
        return tuple(options)

    def _selected_record(self) -> CapabilityRecord | None:
        option = self.query_one("#extension-list", OptionList).highlighted_option
        if option is None or option.id is None:
            return None
        return next((record for record in self.records if record.capability_id == option.id), None)

    def action_close(self) -> None:
        self.post_message(self.Closed())

    def action_toggle_extension(self) -> None:
        record = self._selected_record()
        if record is not None:
            self.post_message(self.Toggle(record.capability_id, not record.enabled))

    def action_inspect(self) -> None:
        record = self._selected_record()
        if record is not None:
            self._show_details(record)

    @on(OptionList.OptionHighlighted, "#extension-list")
    def option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        record = next(
            (item for item in self.records if item.capability_id == event.option.id), None
        )
        if record is not None:
            self._show_details(record)

    @on(OptionList.OptionSelected, "#extension-list")
    def option_selected(self, event: OptionList.OptionSelected) -> None:
        record = next(
            (item for item in self.records if item.capability_id == event.option.id), None
        )
        if record is not None:
            self._show_details(record)

    def _show_details(self, record: CapabilityRecord) -> None:
        permissions = [
            label
            for label, required in (
                ("读", record.permissions.filesystem_read),
                ("写", record.permissions.filesystem_write),
                ("网络", record.permissions.network),
                ("进程", record.permissions.process),
            )
            if required
        ]
        lines = [
            f"名称: {record.public_name}",
            f"类型: {record.kind.value} · 范围: {record.source.scope.value}",
            f"状态: {record.activation.value} · {'已启用' if record.enabled else '已禁用'}",
            f"权限: {'、'.join(permissions) or '无'}",
            f"标识: {record.capability_id}",
        ]
        if record.diagnostics:
            lines.append("诊断: " + "; ".join(item.message for item in record.diagnostics))
        self.query_one("#extension-details", Static).update("\n".join(lines))

    def refresh_records(self, records: tuple[CapabilityRecord, ...]) -> None:
        selected = self._selected_record()
        self.records = records
        listing = self.query_one("#extension-list", OptionList)
        listing.clear_options()
        listing.add_options(self._options())
        if selected is not None:
            for index, option in enumerate(listing.options):
                if option.id == selected.capability_id:
                    listing.highlighted = index
                    break
        details = self._selected_record()
        if details is not None:
            self._show_details(details)


class ExtensionList(Static):
    """Responsive textual representation of the shared extension catalog."""

    DEFAULT_CSS = """
    ExtensionList {
        width: 100%;
        height: auto;
        max-height: 16;
        overflow-y: auto;
        padding: 0 2;
    }
    """

    @staticmethod
    def render_text(records: tuple[CapabilityRecord, ...]) -> str:
        if not records:
            return "扩展能力:\n未发现扩展能力"

        plugin_groups: dict[str, list[CapabilityRecord]] = {}
        standalone: list[CapabilityRecord] = []
        for record in records:
            if record.source.plugin_id is None:
                standalone.append(record)
            else:
                plugin_groups.setdefault(record.source.plugin_id, []).append(record)

        lines = ["扩展能力"]
        if plugin_groups:
            lines.append("  插件")
            for plugin_id, group in sorted(plugin_groups.items()):
                plugin = next((item for item in group if item.kind is CapabilityKind.PLUGIN), None)
                reference = plugin or group[0]
                lines.append(f"    {plugin_id} · {ExtensionList._status(reference)}")
                for kind, label, prefix in (
                    (CapabilityKind.SKILL, "技能", "$"),
                    (CapabilityKind.MCP_SERVER, "MCP", ""),
                    (CapabilityKind.HOOK, "Hook", ""),
                ):
                    names = sorted(item.public_name for item in group if item.kind is kind)
                    if names:
                        lines.append(
                            f"      {label}: {', '.join(f'{prefix}{name}' for name in names)}"
                        )

        for kind, label, prefix in (
            (CapabilityKind.MCP_SERVER, "MCP 服务", ""),
            (CapabilityKind.SKILL, "技能", "$"),
            (CapabilityKind.HOOK, "Hooks", ""),
        ):
            group = [item for item in standalone if item.kind is kind]
            if not group:
                continue
            lines.append(f"  {label}")
            for record in group:
                lines.append(
                    f"    {prefix}{record.public_name} · {record.source.scope.value} · "
                    f"{ExtensionList._status(record)}"
                )

        diagnostics = [diagnostic for record in records for diagnostic in record.diagnostics]
        if diagnostics:
            lines.append("  诊断")
            lines.extend(f"    {item.category}: {item.message}" for item in diagnostics)
        return "\n".join(lines)

    @staticmethod
    def _status(record: CapabilityRecord) -> str:
        state = {
            "available": "可用",
            "active": "运行中",
            "inactive": "已禁用",
            "untrusted": "未信任",
            "failed": "失败",
        }.get(record.activation.value, record.activation.value)
        permissions = [
            name
            for name, required in (
                ("读", record.permissions.filesystem_read),
                ("写", record.permissions.filesystem_write),
                ("网络", record.permissions.network),
                ("进程", record.permissions.process),
            )
            if required
        ]
        suffix = f" · {'、'.join(permissions)}" if permissions else ""
        return f"{state}{suffix}"

    def __init__(self, records: tuple[CapabilityRecord, ...]) -> None:
        super().__init__(self.render_text(records))
