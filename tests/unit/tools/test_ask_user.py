import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from windcode.domain.tools import ToolContext
from windcode.tools.ask_user import AskUserInput, AskUserTool, Question


@pytest.mark.asyncio
async def test_waits_for_run_response_channel(tmp_path: Path) -> None:
    async def respond(payload: object) -> object:
        assert isinstance(payload, tuple)
        return {"choice": "yes"}

    result = await AskUserTool().execute(
        ToolContext(tmp_path, "run", lambda: False, request_user=respond),
        AskUserInput(questions=(Question(id="choice", prompt="Continue?", options=("yes", "no")),)),
    )
    assert result.data["answers"] == {"choice": "yes"}


def test_validates_question_and_option_counts() -> None:
    with pytest.raises(ValidationError):
        Question(id="choice", prompt="Continue?", options=("yes",))
    with pytest.raises(ValidationError):
        AskUserInput(questions=())


@pytest.mark.asyncio
async def test_cancelled_context_does_not_request_input(tmp_path: Path) -> None:
    async def never(_payload: object) -> object:
        raise AssertionError("must not be called")

    with pytest.raises(asyncio.CancelledError):
        await AskUserTool().execute(
            ToolContext(tmp_path, "run", lambda: True, request_user=never),
            AskUserInput(
                questions=(Question(id="choice", prompt="Continue?", options=("yes", "no")),)
            ),
        )
