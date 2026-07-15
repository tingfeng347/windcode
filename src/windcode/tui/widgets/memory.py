from __future__ import annotations

from typing import ClassVar, cast

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Select, Static, Switch
from textual.widgets.option_list import Option

from windcode.memory import MemoryActivation, MemoryRecord, MemoryStatus


class MemoryManager(ModalScreen[None]):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "close", "关闭")]

    class EnabledChanged(Message):
        def __init__(self, enabled: bool) -> None:
            super().__init__()
            self.enabled = enabled

    class Forget(Message):
        def __init__(self, memory_id: str) -> None:
            super().__init__()
            self.memory_id = memory_id

    class ActivationChanged(Message):
        def __init__(self, memory_id: str, activation: MemoryActivation) -> None:
            super().__init__()
            self.memory_id = memory_id
            self.activation = activation

    class Rebuild(Message):
        pass

    class Closed(Message):
        pass

    def __init__(self, records: tuple[MemoryRecord, ...], *, enabled: bool) -> None:
        super().__init__(id="memory-manager")
        self.records = records
        self.enabled = enabled

    def compose(self) -> ComposeResult:
        with Vertical(id="memory-dialog"):
            yield Static("长期记忆", id="memory-manager-title")
            with Horizontal(id="memory-enabled-row"):
                yield Static("启用跨会话记忆", id="memory-enabled-label")
                yield Switch(value=self.enabled, id="memory-enabled")
            yield Static(
                "稳定事实与用户偏好自动保存; 经验遵循 No Execution, No Memory。",
                id="memory-policy",
            )
            yield OptionList(*self._options(), id="memory-list")
            yield Static("选择一条记忆查看详情", id="memory-details")
            yield Select(
                ((item.value, item.value) for item in MemoryActivation),
                id="memory-activation",
                prompt="激活策略",
                allow_blank=False,
                disabled=True,
            )
            with Horizontal(id="memory-actions", classes="dialog-actions"):
                yield Button("忘记所选", id="memory-forget", variant="error")
                yield Button("重建索引", id="memory-rebuild")
                yield Button("关闭", id="memory-close", variant="primary")

    def _options(self) -> tuple[Option, ...]:
        options: list[Option] = []
        for record in self.records:
            text = Text(" ".join(record.summary.split()), style="bold")
            options.append(Option(text, id=record.memory_id))
        return tuple(options)

    def _selected_id(self) -> str | None:
        option = self.query_one("#memory-list", OptionList).highlighted_option
        return option.id if option is not None else None

    @on(OptionList.OptionHighlighted, "#memory-list")
    def highlighted(self, event: OptionList.OptionHighlighted) -> None:
        record = next(item for item in self.records if item.memory_id == event.option.id)
        summary = " ".join(record.summary.split())
        body = " ".join(record.body.split())
        content = (
            record.body if summary == body else f"摘要: {record.summary}\n\n内容: {record.body}"
        )
        self.query_one("#memory-details", Static).update(
            f"类型: {record.kind.value} · 状态: {record.status.value} · "
            f"激活: {record.activation.value} · 优先级: {record.priority}\n\n{content}\n\n"
            f"更新时间: {record.updated_at.isoformat()} · "
            f"置信度: {record.confidence:.0%}"
        )
        selector = cast(Select[str], self.query_one("#memory-activation", Select))
        selector.value = record.activation.value
        selector.disabled = record.status is not MemoryStatus.ACTIVE

    @on(Select.Changed, "#memory-activation")
    def activation_changed(self, event: Select.Changed) -> None:
        memory_id = self._selected_id()
        if memory_id is None or event.value is Select.BLANK:
            return
        record = next(item for item in self.records if item.memory_id == memory_id)
        activation = MemoryActivation(str(event.value))
        if record.status is MemoryStatus.ACTIVE and activation is not record.activation:
            self.post_message(self.ActivationChanged(memory_id, activation))

    @on(Switch.Changed, "#memory-enabled")
    def enabled_changed(self, event: Switch.Changed) -> None:
        if event.value != self.enabled:
            self.enabled = event.value
            self.post_message(self.EnabledChanged(event.value))

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "memory-forget":
            if memory_id := self._selected_id():
                self.post_message(self.Forget(memory_id))
        elif event.button.id == "memory-rebuild":
            self.post_message(self.Rebuild())
        elif event.button.id == "memory-close":
            self.action_close()

    def refresh_records(self, records: tuple[MemoryRecord, ...]) -> None:
        self.records = records
        listing = self.query_one("#memory-list", OptionList)
        listing.clear_options()
        listing.add_options(self._options())
        self.query_one("#memory-details", Static).update("选择一条记忆查看详情")
        cast(Select[str], self.query_one("#memory-activation", Select)).disabled = True

    def action_close(self) -> None:
        self.post_message(self.Closed())
