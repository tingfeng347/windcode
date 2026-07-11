from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import Static

from windcode.domain.events import (
    SubagentBlocked,
    SubagentCancelled,
    SubagentCleanup,
    SubagentCompleted,
    SubagentConflict,
    SubagentEvent,
    SubagentFailed,
    SubagentIntegrated,
    SubagentProgress,
    SubagentQueued,
    SubagentStarted,
)
from windcode.domain.models import Usage

STATUS_LABELS = {
    "queued": "排队",
    "running": "运行",
    "blocked": "阻塞",
    "completed": "完成",
    "failed": "失败",
    "cancelled": "取消",
    "conflict": "冲突",
    "integrated": "已集成",
}


@dataclass(slots=True)
class SubagentViewState:
    subagent_id: str
    task_index: int
    role: str
    task_name: str
    status: str = "queued"
    summary: str = ""
    activity: str = "等待调度"
    usage: Usage = field(default_factory=Usage)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    commit: str | None = None
    verification: tuple[str, ...] = ()
    retained_path: str | None = None

    @property
    def elapsed_seconds(self) -> int:
        if self.created_at is None or self.updated_at is None:
            return 0
        return max(0, int((self.updated_at - self.created_at).total_seconds()))


class SubagentRow(Vertical, can_focus=True):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "toggle_details", "展开详情", priority=True),
    ]

    def __init__(self, state: SubagentViewState) -> None:
        super().__init__(classes="subagent-row")
        self.state = state
        self.expanded = False

    def compose(self) -> ComposeResult:
        yield Static(classes="subagent-summary")
        yield Static(classes="subagent-details")

    def on_mount(self) -> None:
        self.refresh_state()

    def refresh_state(self) -> None:
        status = STATUS_LABELS.get(self.state.status, self.state.status)
        tokens = self.state.usage.input_tokens + self.state.usage.output_tokens
        self.set_classes(f"subagent-row subagent-status-{self.state.status}")
        self.query_one(".subagent-summary", Static).update(
            f"[{status}] {self.state.role} · {self.state.task_name}\n"
            f"{self.state.elapsed_seconds}s · {tokens} tokens · {self.state.activity}"
        )
        details = self.query_one(".subagent-details", Static)
        detail_lines = [self.state.summary] if self.state.summary else []
        if self.state.commit:
            detail_lines.append(f"提交: {self.state.commit}")
        if self.state.verification:
            detail_lines.append("验证: " + "; ".join(self.state.verification))
        if self.state.retained_path:
            detail_lines.append(f"保留 Worktree: {self.state.retained_path}")
        details.update("\n".join(detail_lines))
        details.display = self.expanded and bool(detail_lines)

    def action_toggle_details(self) -> None:
        self.expanded = not self.expanded
        self.refresh_state()


class SubagentGroup(Vertical):
    """One stable, incrementally updated group for a root run's child tasks."""

    def __init__(self) -> None:
        super().__init__(classes="subagent-group")
        self.states: dict[str, SubagentViewState] = {}
        self.rows: dict[str, SubagentRow] = {}

    def compose(self) -> ComposeResult:
        yield Static("子智能体", classes="subagent-group-title")

    async def apply_event(self, event: SubagentEvent) -> None:
        state = self.states.get(event.subagent_id)
        if state is None:
            state = SubagentViewState(
                subagent_id=event.subagent_id,
                task_index=event.task_index,
                role=event.role,
                task_name=event.task_name,
                summary=event.summary,
                created_at=event.created_at,
                updated_at=event.created_at,
            )
            self.states[event.subagent_id] = state
        state.updated_at = event.created_at
        state.summary = event.summary or state.summary
        self._apply_details(state, event)

        row = self.rows.get(event.subagent_id)
        if row is None:
            row = SubagentRow(state)
            self.rows[event.subagent_id] = row
            before = next(
                (
                    existing
                    for child_id, existing in self.rows.items()
                    if child_id != event.subagent_id
                    and self.states[child_id].task_index > state.task_index
                    and existing.is_attached
                ),
                None,
            )
            await self.mount(row, before=before)
        row.refresh_state()

    @staticmethod
    def _apply_details(state: SubagentViewState, event: SubagentEvent) -> None:
        if isinstance(event, SubagentQueued):
            state.status = "queued"
            state.activity = "等待调度"
        elif isinstance(event, SubagentStarted):
            state.status = "running"
            state.activity = "已启动"
        elif isinstance(event, SubagentProgress):
            state.status = "running"
            state.activity = event.activity or "运行中"
            if event.usage != Usage():
                state.usage = event.usage
        elif isinstance(event, SubagentBlocked):
            state.status = "blocked"
            state.activity = event.reason or "需要父智能体处理"
        elif isinstance(event, SubagentCompleted):
            state.status = "completed"
            state.activity = "等待检查或集成"
            state.commit = event.commit
            state.verification = event.verification
            state.usage = event.usage
        elif isinstance(event, SubagentFailed):
            state.status = "failed"
            state.activity = event.message or event.category
            if event.usage != Usage():
                state.usage = event.usage
        elif isinstance(event, SubagentCancelled):
            state.status = "cancelled"
            state.activity = event.reason
            if event.usage != Usage():
                state.usage = event.usage
        elif isinstance(event, SubagentIntegrated):
            state.status = "integrated"
            state.activity = "提交已集成"
            state.commit = event.commit
            state.verification = event.verification
        elif isinstance(event, SubagentConflict):
            state.status = "conflict"
            state.activity = event.message or "集成冲突"
            if event.conflict_files:
                state.verification = ("冲突文件: " + ", ".join(event.conflict_files),)
        elif isinstance(event, SubagentCleanup):
            state.retained_path = event.retained_path
            if event.removed:
                state.activity = "Worktree 已清理"
            elif event.reason:
                state.activity = event.reason
