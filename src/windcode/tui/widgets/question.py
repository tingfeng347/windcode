from __future__ import annotations

from typing import cast

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Button, Label, Select

from windcode.domain.events import UserInputRequested


class QuestionWidget(Vertical):
    class Submitted(Message):
        def __init__(self, request_id: str, answers: dict[str, str]) -> None:
            super().__init__()
            self.request_id = request_id
            self.answers = answers

    def __init__(self, request: UserInputRequested) -> None:
        super().__init__(classes="interaction")
        self.request = request

    def compose(self) -> ComposeResult:
        for question in self.request.questions:
            question_id = str(question.get("id", "question"))
            prompt = str(question.get("prompt", question_id))
            raw_options = question.get("options", ())
            options = tuple(str(option) for option in raw_options)
            yield Label(prompt)
            yield Select(((option, option) for option in options), id=f"question-{question_id}")
        yield Button("提交", id="question-submit", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "question-submit":
            return
        answers: dict[str, str] = {}
        for question in self.request.questions:
            question_id = str(question.get("id", "question"))
            select = cast(Select[str], self.query_one(f"#question-{question_id}", Select))
            value = select.value
            if not isinstance(value, str):
                return
            answers[question_id] = str(value)
        self.post_message(self.Submitted(self.request.request_id, answers))
        self.remove()
