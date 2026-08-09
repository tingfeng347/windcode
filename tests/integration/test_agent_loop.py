from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict

from windcode.config import PermissionMode
from windcode.domain.errors import ErrorCategory, WindcodeError
from windcode.domain.events import RunCompleted, RunFailed, ToolFinished
from windcode.domain.messages import Message, Role, TextBlock, ToolResultBlock
from windcode.domain.models import (
    ModelCompleted,
    ModelEvent,
    ModelRequest,
    StopReason,
    TextDelta,
    ToolCallDelta,
    Usage,
)
from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.observability import TraceStore
from windcode.policy import PolicyEngine
from windcode.providers import ModelTarget
from windcode.runtime import (
    AgentLoop,
    EventBus,
    ModelSession,
    RunBudgets,
    RunControl,
    RunIdentity,
    RunJournal,
    ToolRuntime,
    ToolScheduler,
)
from windcode.sessions import SessionStore
from windcode.tools import ToolRegistry


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class EchoTool:
    name = "echo"
    description = "Echo text."
    input_model = EchoInput
    effects = frozenset({ToolEffect.READ})

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        return ToolResult(cast(EchoInput, arguments).text)


class CodingTransport:
    name = "scripted"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            yield ToolCallDelta("call", "echo", '{"text":"contents"}')
            yield ModelCompleted(StopReason.TOOL_USE, Usage(10, 2))
        else:
            result_block = request.messages[-1].content[0]
            assert isinstance(result_block, ToolResultBlock)
            assert result_block.content == "contents"
            yield TextDelta("task complete")
            yield ModelCompleted(StopReason.STOP, Usage(12, 3))

    async def aclose(self) -> None:
        pass


class FailingTransport:
    name = "failing"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        raise WindcodeError("bad request", ErrorCategory.INVALID_REQUEST)
        yield TextDelta("")

    async def aclose(self) -> None:
        pass


def build_loop(
    tmp_path: Path,
    transport: CodingTransport | FailingTransport,
    *,
    budgets: RunBudgets | None = None,
) -> tuple[AgentLoop, EventBus, SessionStore]:
    session = SessionStore.create(tmp_path / "sessions", "session")
    bus = EventBus(session, TraceStore("run", root=tmp_path / "traces"))
    registry = ToolRegistry()
    registry.register(EchoTool())
    scheduler = ToolScheduler(
        registry, PolicyEngine(PermissionMode.FULL_ACCESS, sandbox_enabled=False)
    )
    loop = AgentLoop(
        identity=RunIdentity("session", "run"),
        model=ModelSession((ModelTarget("scripted", "model", transport),), "system"),
        tools=ToolRuntime(scheduler, RunControl(budgets)),
        journal=RunJournal(bus),
    )
    return loop, bus, session


@pytest.mark.asyncio
async def test_agent_loop_executes_tool_feedback_and_completes(tmp_path: Path) -> None:
    transport = CodingTransport()
    loop, bus, _session = build_loop(tmp_path, transport)

    result = await loop.run("do it", tmp_path)
    events = [event async for event in bus.subscribe()]

    assert result.status == "completed"
    assert result.final_text == "task complete"
    assert len(transport.requests) == 2
    assert any(isinstance(event, ToolFinished) for event in events)
    assert isinstance(events[-1], RunCompleted)


@pytest.mark.asyncio
async def test_agent_loop_stops_at_model_budget(tmp_path: Path) -> None:
    loop, bus, _session = build_loop(
        tmp_path, CodingTransport(), budgets=RunBudgets(max_model_steps=1)
    )

    result = await loop.run("do it", tmp_path)
    events = [event async for event in bus.subscribe()]

    assert result.status == "failed"
    assert isinstance(events[-1], RunFailed)
    assert events[-1].category == "budget"


@pytest.mark.asyncio
async def test_agent_loop_turns_unrecoverable_provider_error_into_terminal_event(
    tmp_path: Path,
) -> None:
    loop, bus, _session = build_loop(tmp_path, FailingTransport())

    result = await loop.run("do it", tmp_path)
    events = [event async for event in bus.subscribe()]

    assert result.status == "failed"
    assert isinstance(events[-1], RunFailed)
    assert events[-1].category == "invalid_request"


class FinalThenInboundTransport:
    name = "inbound"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield TextDelta("initial answer" if len(self.requests) == 1 else "revised answer")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class OneLateMessage:
    def __init__(self) -> None:
        self.pending = True

    async def drain_inbound(self) -> tuple[Message, ...]:
        return ()

    async def drain_or_close_inbound(self) -> tuple[Message, ...]:
        if not self.pending:
            return ()
        self.pending = False
        return (Message(Role.USER, (TextBlock("late sibling message"),)),)


@pytest.mark.asyncio
async def test_agent_loop_processes_message_accepted_at_natural_completion(tmp_path: Path) -> None:
    transport = FinalThenInboundTransport()
    loop, _bus, _session = build_loop(tmp_path, cast(CodingTransport, transport))
    loop.inbound_message_source = OneLateMessage()

    result = await loop.run("do it", tmp_path)

    assert result.final_text == "revised answer"
    assert len(transport.requests) == 2
    late = transport.requests[1].messages[-1].content[0]
    assert isinstance(late, TextBlock)
    assert late.text == "late sibling message"
