from __future__ import annotations

from collections.abc import Callable
from time import monotonic

from rich.text import Text as RichText
from textual.containers import Vertical, VerticalScroll
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from windcode.domain.events import (
    AgentEventType,
    ModelFallback,
    ModelRetrying,
    ModelStarted,
    ReasoningStatus,
    RunCancelled,
    RunCompleted,
    RunFailed,
    TextDeltaEvent,
    ToolStarted,
)
from windcode.domain.messages import Message, Role, TextBlock

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class MessageStream(VerticalScroll):
    """MewCode-style chat stream with one mutable row per model response."""

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        super().__init__(*children, name=name, id=id, classes=classes, disabled=disabled)
        self._clock = clock
        self._ai_row: Vertical | None = None
        self._streaming_label: Static | None = None
        self._accumulated_text = ""
        self._reasoning_text = ""
        self._spinner_label: Static | None = None
        self._spinner_timer: Timer | None = None
        self._spinner_index = 0
        self._started_at = 0.0
        self._thinking_paused_at: float | None = None
        self._thinking_paused_seconds = 0.0
        self._thinking_pause_keys: set[str] = set()
        self._thinking_active = False
        self._finished_thinking_seconds = 0.0
        self._waiting_for_next_model = False

    async def _mount_if_attached(self, widget: Widget) -> None:
        if self.is_attached:
            await self.mount(widget)

    async def add_user_message(self, text: str) -> None:
        content = RichText()
        content.append("❯ ", style="bold color(80)")  # noqa: RUF001
        content.append(text, style="bold color(255)")
        row = Vertical(Static(content, classes="message user-message"), classes="user-row")
        await self._mount_if_attached(row)
        if self.is_attached:
            self.call_after_refresh(self.scroll_end, animate=False)

    async def add_ai_message(self, text: str) -> None:
        content = RichText()
        content.append("● ", style="bold color(99)")
        content.append(text)
        row = Vertical(Static(content, classes="message ai-message"), classes="ai-row")
        await self._mount_if_attached(row)
        if self.is_attached:
            self.call_after_refresh(self.scroll_end, animate=False)

    async def load_history(self, messages: tuple[Message, ...]) -> None:
        await self.clear()
        for message in messages:
            text = "".join(
                block.text for block in message.content if isinstance(block, TextBlock)
            ).strip()
            if not text:
                continue
            if message.role is Role.USER:
                await self.add_user_message(text)
            elif message.role is Role.ASSISTANT:
                await self.add_ai_message(text)

    async def add_system_message(self, text: str, *, error: bool = False) -> None:
        prefix = "✖ " if error else "  "
        classes = "message error-message" if error else "message system-message"
        await self._mount_if_attached(Static(f"{prefix}{text}", classes=classes))
        if self.is_attached:
            self.call_after_refresh(self.scroll_end, animate=False)

    async def begin_run(self) -> None:
        self._ai_row = None
        self._streaming_label = None
        self._accumulated_text = ""
        self._reasoning_text = ""
        self._waiting_for_next_model = False
        await self._new_ai_row()
        self._started_at = self._clock()
        self._thinking_paused_at = None
        self._thinking_paused_seconds = 0.0
        self._thinking_pause_keys.clear()
        self._thinking_active = True
        self._finished_thinking_seconds = 0.0
        self._spinner_index = 0
        self._spinner_label = Static("  ⠋ 思考中...", id="spinner-live")
        await self._mount_if_attached(self._spinner_label)
        if self.is_attached:
            self._spinner_timer = self.set_interval(0.08, self._tick_spinner)
            self.call_after_refresh(self.scroll_end, animate=False)

    async def _new_ai_row(self) -> None:
        self._streaming_label = Static("", classes="message ai-message")
        self._ai_row = Vertical(classes="ai-row")
        await self._mount_if_attached(self._ai_row)
        if self._ai_row.is_attached:
            await self._ai_row.mount(self._streaming_label)
        self._accumulated_text = ""

    async def _ensure_streaming_label(self) -> Static | None:
        if self._ai_row is None:
            await self._new_ai_row()
        if self._streaming_label is None and self._ai_row is not None:
            self._streaming_label = Static("", classes="message ai-message")
            if self._ai_row.is_attached:
                await self._ai_row.mount(self._streaming_label)
        return self._streaming_label

    async def _append_text(self, text: str) -> None:
        label = await self._ensure_streaming_label()
        self._accumulated_text += text
        if label is not None:
            content = RichText()
            content.append("● ", style="bold color(99)")
            content.append(self._accumulated_text)
            label.update(content)
        if self.is_attached:
            self.call_after_refresh(self.scroll_end, animate=False)

    async def begin_block(self) -> None:
        if self._streaming_label is not None and not self._accumulated_text:
            await self._streaming_label.remove()
        self._streaming_label = None
        self._accumulated_text = ""

    async def mount_in_ai_row(self, widget: Widget) -> None:
        if self._ai_row is None:
            await self._new_ai_row()
        if self._ai_row is not None:
            await self._ai_row.mount(widget)

    def _tick_spinner(self) -> None:
        self._spinner_index += 1
        frame = SPINNER_FRAMES[self._spinner_index % len(SPINNER_FRAMES)]
        if self._spinner_label is not None:
            if self._thinking_pause_keys:
                self._spinner_label.update("  等待审批...")
            else:
                self._spinner_label.update(f"  {frame} 思考中...  ({self.thinking_seconds:.0f}s)")

    @property
    def thinking_seconds(self) -> float:
        if not self._thinking_active:
            return self._finished_thinking_seconds
        now = self._clock()
        paused = self._thinking_paused_seconds
        if self._thinking_paused_at is not None:
            paused += now - self._thinking_paused_at
        return max(0.0, now - self._started_at - paused)

    def pause_thinking(self, key: str) -> None:
        if not self._thinking_active or key in self._thinking_pause_keys:
            return
        if not self._thinking_pause_keys:
            self._thinking_paused_at = self._clock()
        self._thinking_pause_keys.add(key)

    def resume_thinking(self, key: str) -> None:
        if key not in self._thinking_pause_keys:
            return
        self._thinking_pause_keys.remove(key)
        if not self._thinking_pause_keys and self._thinking_paused_at is not None:
            self._thinking_paused_seconds += self._clock() - self._thinking_paused_at
            self._thinking_paused_at = None

    async def finish_run(self) -> None:
        if self._streaming_label is not None and not self._accumulated_text:
            await self._streaming_label.remove()
            self._streaming_label = None
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        if self._spinner_label is not None:
            await self._spinner_label.remove()
            self._spinner_label = None
        elapsed = self.thinking_seconds
        self._finished_thinking_seconds = elapsed
        self._thinking_active = False
        self._thinking_paused_at = None
        self._thinking_pause_keys.clear()
        if self._ai_row is not None and self._ai_row.is_attached:
            await self._ai_row.mount(
                Static(
                    f"✻ 已思考 {elapsed:.1f} 秒",
                    classes="message thinking-done",
                )
            )

    async def clear(self) -> None:
        await self.finish_run()
        self._ai_row = None
        self._streaming_label = None
        self._accumulated_text = ""
        self._reasoning_text = ""
        if self.is_attached:
            await self.remove_children()

    async def apply_event(self, event: AgentEventType) -> None:
        if isinstance(event, TextDeltaEvent):
            await self._append_text(event.text)
        elif isinstance(event, ReasoningStatus):
            # Provider reasoning arrives as deltas. MewCode keeps it out of chat history.
            self._reasoning_text += event.status
        elif isinstance(event, ModelStarted):
            if self._waiting_for_next_model:
                await self._new_ai_row()
                self._waiting_for_next_model = False
        elif isinstance(event, ModelRetrying):
            await self.add_system_message(f"正在重试: {event.reason}")
        elif isinstance(event, ModelFallback):
            await self.add_system_message(
                f"模型已切换: {event.from_model} -> {event.to_model}; 原因: {event.reason}"
            )
        elif isinstance(event, ToolStarted):
            await self.begin_block()
            self._waiting_for_next_model = True
        elif isinstance(event, RunCompleted):
            await self.finish_run()
        elif isinstance(event, RunFailed):
            await self.finish_run()
            await self.add_system_message(f"{event.category}: {event.message}", error=True)
        elif isinstance(event, RunCancelled):
            await self.finish_run()
            await self.add_system_message("操作已取消")
