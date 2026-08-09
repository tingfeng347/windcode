from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.run_builder_support import child_preparer
from windcode.config import AppConfig, PermissionMode, SandboxConfig
from windcode.domain.events import ApprovalRequested, RunStarted
from windcode.domain.messages import TextBlock
from windcode.domain.models import ModelCompleted, ModelEvent, ModelRequest, StopReason, TextDelta
from windcode.domain.subagents import (
    CollaborationContribution,
    SubagentMessage,
    SubagentRecord,
    SubagentRole,
    SubagentTaskKind,
    SubagentTaskSpec,
)
from windcode.domain.tools import ToolContext
from windcode.providers import ModelTarget
from windcode.runtime.loop import AgentBlocked
from windcode.runtime.scheduler import ScheduledCall
from windcode.runtime.subagents import ChildRunProfile
from windcode.runtime.subagents.approvals import ApprovalRouter
from windcode.runtime.subagents.budgets import AggregateBudget
from windcode.runtime.subagents.collaboration import BoundSubagentCollaboration
from windcode.sessions import SessionStatus, SessionStore
from windcode.tools import create_builtin_registry


class RecordingTransport:
    name = "recording"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield TextDelta("child complete")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class EmptyCollaborationBackend:
    def __init__(self, records: tuple[SubagentRecord, ...]) -> None:
        self.records = records

    def list_peers(self, sender_subagent_id: str) -> tuple[SubagentRecord, ...]:
        del sender_subagent_id
        return self.records

    async def send_message(
        self, sender_subagent_id: str, target: str, content: str
    ) -> SubagentMessage:
        del sender_subagent_id, target, content
        raise AssertionError("unexpected send")

    async def receive_messages(
        self,
        recipient_subagent_id: str,
        *,
        max_messages: int,
        timeout_seconds: float | None = None,
        close_if_empty: bool = False,
    ) -> tuple[SubagentMessage, ...]:
        del recipient_subagent_id, max_messages, timeout_seconds, close_if_empty
        return ()

    async def exchange_coordination_round(
        self,
        subagent_id: str,
        round_index: int,
        contribution: str,
        timeout_seconds: float,
    ) -> tuple[CollaborationContribution, ...]:
        del subagent_id, round_index, contribution, timeout_seconds
        return ()


def record(index: int) -> SubagentRecord:
    spec = SubagentTaskSpec(
        f"inspect_{index}",
        SubagentRole.RESEARCHER,
        SubagentTaskKind.READ,
        f"inspect module {index}",
        f"private child context {index}",
        "report",
        ("cite files",),
    )
    return SubagentRecord(f"child-{index}", "parent", "run", index, spec)


def worker_read_record(index: int) -> SubagentRecord:
    spec = SubagentTaskSpec(
        f"worker_inspect_{index}",
        SubagentRole.WORKER,
        SubagentTaskKind.READ,
        f"inspect module {index}",
        f"private child context {index}",
        "report findings to the parent",
        ("cite files",),
        frozenset({"read_file", "write_file", "edit_file", "apply_patch", "shell"}),
    )
    return SubagentRecord(f"worker-child-{index}", "parent", "run", index, spec)


async def test_child_factory_creates_fresh_isolated_runtime(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("PROJECT-INSTRUCTION", encoding="utf-8")
    transport = RecordingTransport()
    target = ModelTarget("recording", "model", transport)
    state = tmp_path / "state"
    registry = create_builtin_registry()
    prepare_child = child_preparer(
        config=AppConfig(),
        state_root=state,
        parent_tools=registry,
        model_chain=lambda _model: (target,),
    )

    async def publish(_event: ApprovalRequested) -> None:
        pass

    approvals = ApprovalRouter(parent_session_id="parent", parent_run_id="run", publish=publish)
    aggregate = AggregateBudget(
        max_model_steps=10,
        max_tool_calls=10,
        max_runtime_seconds=60,
    )
    first_record = record(1)
    second_record = record(2)
    backend = EmptyCollaborationBackend((first_record, second_record))
    first = prepare_child(
        ChildRunProfile(
            first_record,
            workspace,
            PermissionMode.DEFAULT,
            aggregate,
            approvals,
            BoundSubagentCollaboration(backend, first_record.subagent_id),
        )
    )
    second = prepare_child(
        ChildRunProfile(
            second_record,
            workspace,
            PermissionMode.DEFAULT,
            aggregate,
            approvals,
            BoundSubagentCollaboration(backend, second_record.subagent_id),
        )
    )

    assert first.control is not second.control
    assert first.event_bus is not second.event_bus
    assert first.loop.scheduler.registry is not second.loop.scheduler.registry
    assert "ask_user" not in first.loop.scheduler.registry.names()
    assert {"list_agents", "send_message", "wait_for_messages"} <= set(
        first.loop.scheduler.registry.names()
    )
    assert not any(name.endswith("_subagent") for name in first.loop.scheduler.registry.names())
    assert "Return all findings in your final response" in first.prompt
    assert "Do not create, edit, or save a report file" in first.prompt

    first_result = await first.loop.run(first.prompt, workspace)
    second_result = await second.loop.run(second.prompt, workspace)
    assert first_result.final_text == second_result.final_text == "child complete"
    assert len(transport.requests) == 2
    first_prompt = transport.requests[0].messages[0].content[0]
    assert isinstance(first_prompt, TextBlock)
    assert "private child context 1" in first_prompt.text
    assert "private child context 2" not in first_prompt.text
    assert "PARENT-HISTORY-MARKER" not in transport.requests[0].system_prompt
    assert "PROJECT-INSTRUCTION" in transport.requests[0].system_prompt
    assert first.event_bus.trace_store.path != second.event_bus.trace_store.path
    child_session_id = first.record.child_session_id
    assert child_session_id is not None
    assert (
        SessionStore.open(state / "sessions", child_session_id).metadata.status
        is SessionStatus.COMPLETED
    )
    with pytest.raises(RuntimeError, match="event bus is closed"):
        await first.event_bus.publish(
            RunStarted(
                event_id="late",
                session_id=child_session_id,
                run_id="late",
                turn=0,
                prompt="late",
            )
        )


async def test_worker_read_runtime_does_not_register_write_tools(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = RecordingTransport()
    prepare_child = child_preparer(
        config=AppConfig(),
        state_root=tmp_path / "state",
        parent_tools=create_builtin_registry(),
        model_chain=lambda _model: (ModelTarget("recording", "model", transport),),
    )

    async def publish(_event: ApprovalRequested) -> None:
        pass

    runtime = prepare_child(
        ChildRunProfile(
            worker_read_record(1),
            workspace,
            PermissionMode.FULL_ACCESS,
            AggregateBudget(
                max_model_steps=10,
                max_tool_calls=10,
                max_runtime_seconds=60,
            ),
            ApprovalRouter(
                parent_session_id="parent",
                parent_run_id="run",
                publish=publish,
            ),
        )
    )

    names = set(runtime.loop.scheduler.registry.names())
    assert {"read_file", "shell"} <= names
    assert {"write_file", "edit_file", "apply_patch"}.isdisjoint(names)
    assert "Do not create, edit, or save a report file" in runtime.prompt


async def test_read_child_rejects_shell_write_and_preserves_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = workspace / "original.txt"
    original.write_text("unchanged\n", encoding="utf-8")
    transport = RecordingTransport()
    prepare_child = child_preparer(
        config=AppConfig(sandbox=SandboxConfig(enabled=False)),
        state_root=tmp_path / "state",
        parent_tools=create_builtin_registry(),
        model_chain=lambda _model: (ModelTarget("recording", "model", transport),),
    )

    async def publish(_event: ApprovalRequested) -> None:
        pass

    runtime = prepare_child(
        ChildRunProfile(
            record(1),
            workspace,
            PermissionMode.FULL_ACCESS,
            AggregateBudget(
                max_model_steps=10,
                max_tool_calls=10,
                max_runtime_seconds=60,
            ),
            ApprovalRouter(parent_session_id="parent", parent_run_id="run", publish=publish),
        )
    )
    context = ToolContext(workspace, "child-run", lambda: False)
    (result,) = await runtime.loop.scheduler.execute(
        (ScheduledCall("write", "shell", {"command": "printf changed > original.txt"}),),
        context,
    )

    assert result.result.is_error
    assert result.result.data["error"] == "policy_denied"
    assert original.read_text(encoding="utf-8") == "unchanged\n"

    direct = await runtime.loop.scheduler.registry.execute(
        "shell",
        context,
        {"command": "true", "network": True},
    )
    assert not direct.is_error


async def test_child_user_question_call_becomes_blocked(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = RecordingTransport()
    prepare_child = child_preparer(
        config=AppConfig(),
        state_root=tmp_path / "state",
        parent_tools=create_builtin_registry(),
        model_chain=lambda _model: (ModelTarget("recording", "model", transport),),
    )

    async def publish(_event: ApprovalRequested) -> None:
        pass

    runtime = prepare_child(
        ChildRunProfile(
            record(1),
            workspace,
            PermissionMode.DEFAULT,
            AggregateBudget(
                max_model_steps=10,
                max_tool_calls=10,
                max_runtime_seconds=60,
            ),
            ApprovalRouter(parent_session_id="parent", parent_run_id="run", publish=publish),
        )
    )
    assert "ask_user" not in runtime.loop.scheduler.registry.names()
    with pytest.raises(AgentBlocked, match="clarification is required"):
        await runtime.loop.scheduler.execute(
            (ScheduledCall("question", "ask_user", {"questions": []}),),
            ToolContext(workspace, "child-run", lambda: False),
        )
