from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from windcode.domain.events import ApprovalRequested, UserInputRequested
from windcode.tui.widgets import ApprovalWidget, QuestionWidget


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


class QuestionApp(App[None]):
    def __init__(self, request: UserInputRequested) -> None:
        super().__init__()
        self.request = request
        self.answers: dict[str, str] | None = None

    def compose(self) -> ComposeResult:
        yield QuestionWidget(self.request)

    def on_question_widget_submitted(self, event: QuestionWidget.Submitted) -> None:
        self.answers = event.answers


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


@pytest.mark.asyncio
async def test_question_widget_submits_selected_answer() -> None:
    request = UserInputRequested(
        event_id="event",
        session_id="session",
        run_id="run",
        turn=1,
        request_id="question-request",
        questions=({"id": "strategy", "prompt": "选择方案", "options": ("方案 A", "方案 B")},),
    )
    app = QuestionApp(request)

    async with app.run_test() as pilot:
        await pilot.press("enter", "down", "down", "enter")
        await pilot.pause()
        await pilot.click("#question-submit")
        await pilot.pause()

        assert app.answers == {"strategy": "方案 B"}
