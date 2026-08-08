from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from windcode.domain.events import ApprovalRequested
from windcode.tui.widgets import ApprovalWidget


class ApprovalApp(App[None]):
    def __init__(self, request: ApprovalRequested) -> None:
        super().__init__()
        self.request = request
        self.decision: str | None = None

    def compose(self) -> ComposeResult:
        yield ApprovalWidget(self.request)

    def on_approval_widget_decision(self, event: ApprovalWidget.Decision) -> None:
        self.decision = event.decision


class MultipleApprovalsApp(App[None]):
    def __init__(self, requests: tuple[ApprovalRequested, ...]) -> None:
        super().__init__()
        self.requests = requests

    def compose(self) -> ComposeResult:
        for request in self.requests:
            yield ApprovalWidget(request)


@pytest.mark.asyncio
async def test_approval_buttons_emit_selected_decision(tmp_path: Path) -> None:
    del tmp_path
    request = ApprovalRequested(
        event_id="event",
        session_id="session",
        run_id="run",
        turn=1,
        request_id="request",
        summary="Write file",
        risk="low",
        choices=("allow_once", "allow_session", "deny"),
    )
    app = ApprovalApp(request)
    async with app.run_test() as pilot:
        await pilot.press("down", "down", "enter")
        await pilot.pause()
        assert app.decision == "deny"


@pytest.mark.asyncio
async def test_multiple_approval_widgets_have_unique_ids() -> None:
    requests = tuple(
        ApprovalRequested(
            event_id=f"event-{index}",
            session_id="session",
            run_id="run",
            turn=1,
            request_id=f"request-{index}",
            summary="执行工具: shell",
            risk="high",
            choices=("allow_once", "allow_session", "deny"),
        )
        for index in range(2)
    )
    app = MultipleApprovalsApp(requests)
    async with app.run_test() as pilot:
        await pilot.pause()
        widgets = list(app.query(ApprovalWidget))
        assert len(widgets) == 2
        assert {widget.id for widget in widgets} == {
            "approval-request-0",
            "approval-request-1",
        }
