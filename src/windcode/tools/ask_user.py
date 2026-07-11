from __future__ import annotations

import asyncio
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    options: tuple[str, ...] = Field(min_length=2, max_length=3)


class AskUserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: tuple[Question, ...] = Field(min_length=1, max_length=3)


class AskUserTool:
    name = "ask_user"
    description = "Ask one to three multiple-choice questions through the active run channel."
    input_model = AskUserInput
    effects = frozenset({ToolEffect.USER_INTERACTION})

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        parsed = cast(AskUserInput, arguments)
        if context.request_user is None:
            return ToolResult(
                output="no user response channel is available",
                is_error=True,
                data={"error": "interaction_unavailable"},
            )
        if context.cancelled():
            raise asyncio.CancelledError
        payload = tuple(question.model_dump(mode="json") for question in parsed.questions)
        response = await context.request_user(payload)
        if not isinstance(response, dict):
            return ToolResult(
                output="user response channel returned an invalid response",
                is_error=True,
                data={"error": "invalid_user_response"},
            )
        raw_answers = cast(dict[object, object], response)
        answers = {str(key): str(value) for key, value in raw_answers.items()}
        expected = {question.id for question in parsed.questions}
        if set(answers) != expected:
            return ToolResult(
                output="user response did not answer every question",
                is_error=True,
                data={"error": "incomplete_user_response"},
            )
        return ToolResult(output="user answered the questions", data={"answers": answers})
