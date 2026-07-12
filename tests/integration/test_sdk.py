import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict

from windcode import Windcode
from windcode.domain.events import (
    AgentEventType,
    ApprovalRequested,
    ApprovalResponse,
    RunRequest,
    SubagentEvent,
)
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
from windcode.sdk import RunHandle
from windcode.sessions import SessionStore
from windcode.types import SubagentRecord, SubagentStatus


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


class RepeatedShellTransport:
    name = "repeated-shell"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if request.messages[-1].role is Role.USER:
            yield ToolCallDelta("shell", "shell", '{"command":"printf ok"}')
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        yield TextDelta("done")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class DelegatingTransport:
    name = "delegating"

    def __init__(self) -> None:
        self.root_tools: list[tuple[str, ...]] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if "## 子智能体约束" in request.system_prompt:
            yield TextDelta("child complete")
            yield ModelCompleted(StopReason.STOP)
            return
        self.root_tools.append(tuple(tool.name for tool in request.tools))
        if request.messages[-1].role is Role.USER:
            block = request.messages[-1].content[0]
            assert isinstance(block, TextBlock)
            task_name = f"task_{block.text}"
            arguments = {
                "tasks": [
                    {
                        "task_name": task_name,
                        "role": "researcher",
                        "kind": "read",
                        "goal": "inspect",
                        "context": "self-contained",
                        "expected_output": "report",
                        "verification": ["cite evidence"],
                    }
                ]
            }
            yield ToolCallDelta("spawn", "spawn_subagents", json.dumps(arguments))
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        await asyncio.sleep(0.05)
        yield TextDelta("root complete")
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
async def test_session_shell_approval_is_restored_on_next_run(tmp_path: Path) -> None:
    config = {"sandbox": {"enabled": False}}
    async with Windcode.open(config, state_root=tmp_path / "state") as client:
        client.register_transport("shell", "model", RepeatedShellTransport(), primary=True)

        first = client.start_run(RunRequest("first", tmp_path, session_id="session"))
        first_approvals = 0
        async for event in first:
            if isinstance(event, ApprovalRequested):
                first_approvals += 1
                assert event.tool_name == "shell"
                assert event.arguments_summary == "printf ok"
                await first.respond(ApprovalResponse(event.request_id, "allow_session"))
        await first.result()

        second = client.start_run(RunRequest("second", tmp_path, session_id="session"))
        second_approvals = 0
        async for event in second:
            if isinstance(event, ApprovalRequested):
                second_approvals += 1
                await second.respond(ApprovalResponse(event.request_id, "allow_once"))
        result = await second.result()

    assert first_approvals == 1
    assert second_approvals == 0
    assert result.final_text == "done"


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


@pytest.mark.asyncio
async def test_concurrent_root_runs_have_isolated_subagent_coordinators(tmp_path: Path) -> None:
    transport = DelegatingTransport()
    async with Windcode.open(state_root=tmp_path / "state") as client:
        client.register_transport("delegate", "model", transport, primary=True)
        first = client.start_run(RunRequest("root_a", tmp_path, permission_mode="full_access"))
        second = client.start_run(RunRequest("root_b", tmp_path, permission_mode="full_access"))

        async def collect(handle: RunHandle) -> list[AgentEventType]:
            events: list[AgentEventType] = []
            async for event in handle:
                events.append(event)
            return events

        first_events, second_events, _, _ = await asyncio.gather(
            collect(first), collect(second), first.result(), second.result()
        )

        assert [record.spec.task_name for record in first.subagents()] == ["task_root_a"]
        assert [record.spec.task_name for record in second.subagents()] == ["task_root_b"]
        assert first.subagents()[0].status is SubagentStatus.COMPLETED
        assert second.subagents()[0].status is SubagentStatus.COMPLETED
        assert all("spawn_subagents" in tools for tools in transport.root_tools)
        for events in (first_events, second_events):
            child_events = [event for event in events if isinstance(event, SubagentEvent)]
            assert [event.kind for event in child_events if event.kind != "subagent_progress"] == [
                "subagent_queued",
                "subagent_started",
                "subagent_completed",
            ]
            sequences = [
                event.sequence for event in child_events if event.kind != "subagent_progress"
            ]
            assert all(sequence is not None for sequence in sequences)
            assert sequences == sorted(set(sequences), key=lambda value: value or 0)
        with pytest.raises(RuntimeError, match="after the parent run"):
            await first.cancel_subagent(first.subagents()[0].subagent_id)


def test_public_subagent_types_are_importable() -> None:
    assert SubagentRecord.__name__ == "SubagentRecord"
    assert SubagentStatus.QUEUED.value == "queued"


def test_state_root_migration_copies_every_file_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "user-state"
    target = tmp_path / "workspace" / ".windcode" / "state"
    (source / "sessions" / "one").mkdir(parents=True)
    (source / "sessions" / "one" / "meta.json").write_text("{}", encoding="utf-8")
    (source / "traces").mkdir()
    (source / "traces" / "run.jsonl").write_text('{"event":"done"}\n', encoding="utf-8")

    Windcode._migrate_state_root(source, target)

    assert Windcode._state_manifest(target) == Windcode._state_manifest(source)
    assert (source / "sessions" / "one" / "meta.json").is_file()
    assert (target / "sessions" / "one" / "meta.json").is_file()


def test_state_root_migration_supports_target_nested_below_legacy_root(tmp_path: Path) -> None:
    source = tmp_path / "windcode"
    target = source / "state"
    (source / "sessions").mkdir(parents=True)
    (source / "sessions" / "meta.json").write_text("{}", encoding="utf-8")

    Windcode._migrate_state_root(source, target)

    assert (target / "sessions" / "meta.json").is_file()
    assert (source / "sessions" / "meta.json").is_file()
