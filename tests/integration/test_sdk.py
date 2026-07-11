from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict

from windcode import Windcode
from windcode.domain.events import RunRequest
from windcode.domain.messages import Role, TextBlock
from windcode.domain.models import (
    ModelCompleted,
    ModelEvent,
    ModelRequest,
    StopReason,
    TextDelta,
    ToolCallDelta,
)
from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.sessions import SessionStore


class CustomInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


class CustomTool:
    name = "custom"
    description = "Return a custom value."
    input_model = CustomInput
    effects = frozenset({ToolEffect.READ})

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        return ToolResult(cast(CustomInput, arguments).value)


class CustomTransport:
    name = "custom"

    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        if self.calls == 1:
            assert any(tool.name == "custom" for tool in request.tools)
            yield ToolCallDelta("call", "custom", '{"value":"external"}')
            yield ModelCompleted(StopReason.TOOL_USE)
        else:
            yield TextDelta("custom complete")
            yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        self.closed = True


class HistoryTransport:
    name = "history"

    def __init__(self, *responses: str) -> None:
        self.responses = iter(responses)
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield TextDelta(next(self.responses))
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_custom_tool_and_transport_use_public_runtime_path(tmp_path: Path) -> None:
    transport = CustomTransport()
    async with Windcode.open(state_root=tmp_path / "state") as client:
        client.register_tool(CustomTool())
        client.register_transport("custom", "model", transport, primary=True)
        result = await client.start_run(RunRequest("task", tmp_path)).result()

    assert result.final_text == "custom complete"
    assert transport.calls == 2
    assert transport.closed


def test_start_run_requires_async_context(tmp_path: Path) -> None:
    client = Windcode.open(state_root=tmp_path / "state")
    with pytest.raises(RuntimeError, match="async context"):
        client.start_run(RunRequest("task", tmp_path))


@pytest.mark.asyncio
async def test_resume_restores_messages_from_current_session_branch(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first_transport = HistoryTransport("first response")
    async with Windcode.open(state_root=state) as client:
        client.register_transport("history", "model", first_transport, primary=True)
        await client.start_run(RunRequest("first prompt", tmp_path, session_id="session")).result()

    resumed_transport = HistoryTransport("second response", "branch response")
    async with Windcode.open(state_root=state) as client:
        client.register_transport("history", "model", resumed_transport, primary=True)
        await client.start_run(RunRequest("second prompt", tmp_path, session_id="session")).result()

        resumed_messages = resumed_transport.requests[0].messages
        assert [message.role for message in resumed_messages] == [
            Role.USER,
            Role.ASSISTANT,
            Role.USER,
        ]
        assert [message.content for message in resumed_messages] == [
            (TextBlock("first prompt"),),
            (TextBlock("first response"),),
            (TextBlock("second prompt"),),
        ]

        store = SessionStore.open(state / "sessions", "session")
        first_assistant = next(
            record
            for record in store.load_records()
            if record.record_type == "conversation_message"
            and record.payload["role"] == Role.ASSISTANT.value
        )
        client.rewind_session("session", first_assistant.record_id)
        await client.start_run(RunRequest("branch prompt", tmp_path, session_id="session")).result()

    branch_messages = resumed_transport.requests[1].messages
    assert [message.role for message in branch_messages] == [
        Role.USER,
        Role.ASSISTANT,
        Role.USER,
    ]
    assert [message.content for message in branch_messages] == [
        (TextBlock("first prompt"),),
        (TextBlock("first response"),),
        (TextBlock("branch prompt"),),
    ]
