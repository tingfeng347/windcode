from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.widgets import Static

from windcode.config import AppConfig
from windcode.domain.events import (
    ApprovalRequested,
    SubagentCleanup,
    SubagentCompleted,
    SubagentProgress,
    SubagentQueued,
    SubagentStarted,
)
from windcode.domain.models import Usage
from windcode.domain.subagents import (
    SubagentRecord,
    SubagentRole,
    SubagentStatus,
    SubagentTaskKind,
    SubagentTaskSpec,
)
from windcode.sdk import RunHandle
from windcode.tui import WindcodeApp
from windcode.tui.widgets import ApprovalWidget, ChatInput, SubagentGroup, SubagentRow


class SubagentApp(App[None]):
    CSS = """
    SubagentGroup { width: 100%; height: auto; }
    SubagentRow { width: 100%; height: auto; min-height: 2; }
    .subagent-summary, .subagent-details { width: 100%; height: auto; }
    """

    def compose(self) -> ComposeResult:
        yield SubagentGroup()


class ApprovalApp(App[None]):
    def __init__(self, request: ApprovalRequested) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        yield ApprovalWidget(self.request)


def event_fields(event_id: str, *, offset: int = 0) -> dict[str, object]:
    return {
        "event_id": event_id,
        "session_id": "parent-session",
        "run_id": "parent-run",
        "turn": 1,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=offset),
        "parent_run_id": "parent-run",
        "subagent_id": "child-1",
        "task_index": 0,
        "role": "worker",
        "task_name": "edit_config",
        "summary": "更新配置并验证",
    }


@pytest.mark.asyncio
async def test_subagent_events_update_one_stable_row() -> None:
    app = SubagentApp()
    async with app.run_test(size=(80, 24)) as pilot:
        group = app.query_one(SubagentGroup)
        await group.apply_event(SubagentQueued(**event_fields("queued")))  # type: ignore[arg-type]
        await group.apply_event(
            SubagentStarted(**event_fields("started", offset=1), workspace="/tmp/worktree")  # type: ignore[arg-type]
        )
        await group.apply_event(
            SubagentProgress(
                **event_fields("progress", offset=2),  # type: ignore[arg-type]
                activity="运行测试",
                usage=Usage(input_tokens=20, output_tokens=10),
            )
        )
        await group.apply_event(
            SubagentProgress(
                **event_fields("reasoning", offset=2),  # type: ignore[arg-type]
                activity="继续分析",
            )
        )
        await group.apply_event(
            SubagentCompleted(
                **event_fields("completed", offset=3),  # type: ignore[arg-type]
                commit="abc123",
                verification=("pytest: passed",),
                usage=Usage(input_tokens=30, output_tokens=12),
            )
        )
        await group.apply_event(
            SubagentCleanup(
                **event_fields("cleanup", offset=4),  # type: ignore[arg-type]
                retained_path="/tmp/retained-child-1",
                reason="包含未提交修改",
            )
        )
        await pilot.pause()

        rows = list(group.query(SubagentRow))
        assert len(rows) == 1
        assert rows[0].state.status == "completed"
        assert rows[0].state.usage == Usage(input_tokens=30, output_tokens=12)
        rows[0].action_toggle_details()
        details = rows[0].query_one(".subagent-details", Static)
        assert "abc123" in str(details.content)
        assert "pytest: passed" in str(details.content)
        assert "/tmp/retained-child-1" in str(details.content)


@pytest.mark.asyncio
async def test_subagent_approval_renders_source_tool_arguments_and_risk() -> None:
    app = ApprovalApp(
        request=ApprovalRequested(
            event_id="approval",
            session_id="parent-session",
            run_id="parent-run",
            turn=1,
            request_id="request-1",
            summary="执行写操作",
            risk="high",
            choices=("allow_once", "deny"),
            subagent_id="child-1",
            subagent_role="worker",
            tool_name="shell",
            arguments_summary="uv run pytest -q",
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        content = str(app.query_one("#approval-content", Static).content)
        plain = RichText.from_markup(content).plain
        assert "高风险" in plain
        assert "child-1 · worker" in plain
        assert "bash: uv run pytest -q" in plain


class SnapshotHandle:
    done = False

    def __init__(self, records: tuple[SubagentRecord, ...]) -> None:
        self.records = records

    def subagents(self) -> tuple[SubagentRecord, ...]:
        return self.records

    async def cancel(self) -> None:
        self.done = True


def record(tmp_path: Path) -> SubagentRecord:
    return SubagentRecord(
        subagent_id="child-1",
        parent_session_id="session",
        parent_run_id="run",
        task_index=0,
        spec=SubagentTaskSpec(
            task_name="edit_config",
            role=SubagentRole.WORKER,
            kind=SubagentTaskKind.WRITE,
            goal="更新配置",
            context="独立任务上下文",
            expected_output="提交变更",
            verification=("pytest",),
        ),
        status=SubagentStatus.COMPLETED,
        worktree_path=tmp_path / "retained-worktree",
    )


@pytest.mark.asyncio
async def test_agents_command_reports_empty_and_active_snapshots(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(80, 24)) as pilot:
        prompt = app.query_one("#chat-input", ChatInput)
        prompt.insert("/agents")
        await pilot.press("enter")
        await pilot.pause()
        assert "当前没有子智能体任务" in str(app.query_one("#welcome-notice", Static).content)

        app.handle = cast(RunHandle, SnapshotHandle((record(tmp_path),)))
        prompt.insert("/agents")
        await pilot.press("enter")
        await pilot.pause()
        notice = str(app.query_one("#welcome-notice", Static).content)
        assert "edit_config · worker · completed" in notice
        assert str(tmp_path / "retained-worktree") in notice


@pytest.mark.parametrize("size", [(40, 24), (80, 24), (120, 36)])
@pytest.mark.asyncio
async def test_subagent_rows_fit_without_overlap(size: tuple[int, int]) -> None:
    app = SubagentApp()
    async with app.run_test(size=size) as pilot:
        group = app.query_one(SubagentGroup)
        await group.apply_event(SubagentQueued(**event_fields("queued")))  # type: ignore[arg-type]
        await pilot.pause()
        row = group.query_one(SubagentRow)
        summary = row.query_one(".subagent-summary", Static)

        assert group.region.right <= size[0]
        assert row.region.right <= group.region.right
        assert summary.region.right <= row.region.right
        assert summary.region.bottom <= row.region.bottom
