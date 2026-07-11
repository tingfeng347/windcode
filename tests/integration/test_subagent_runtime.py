from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from windcode.config import AppConfig, PermissionMode, SandboxConfig
from windcode.domain.events import ApprovalRequested
from windcode.domain.messages import TextBlock
from windcode.domain.models import ModelCompleted, ModelEvent, ModelRequest, StopReason, TextDelta
from windcode.domain.subagents import (
    SubagentRecord,
    SubagentRole,
    SubagentTaskKind,
    SubagentTaskSpec,
)
from windcode.domain.tools import ToolContext
from windcode.providers import ModelTarget
from windcode.runtime.loop import AgentBlocked
from windcode.runtime.scheduler import ScheduledCall
from windcode.runtime.subagents.approvals import ApprovalRouter
from windcode.runtime.subagents.budgets import AggregateBudget
from windcode.runtime.subagents.factory import ChildRuntimeFactory
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


async def test_child_factory_creates_fresh_isolated_runtime(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("PROJECT-INSTRUCTION", encoding="utf-8")
    transport = RecordingTransport()
    target = ModelTarget("recording", "model", transport)
    state = tmp_path / "state"
    registry = create_builtin_registry()
    factory = ChildRuntimeFactory(
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
    first = factory.create(
        record(1),
        workspace=workspace,
        parent_permission=PermissionMode.DEFAULT,
        aggregate_budget=aggregate,
        approval_router=approvals,
    )
    second = factory.create(
        record(2),
        workspace=workspace,
        parent_permission=PermissionMode.DEFAULT,
        aggregate_budget=aggregate,
        approval_router=approvals,
    )

    assert first.control is not second.control
    assert first.event_bus is not second.event_bus
    assert first.loop.scheduler.registry is not second.loop.scheduler.registry
    assert "ask_user" not in first.loop.scheduler.registry.names()
    assert not any(name.endswith("_subagent") for name in first.loop.scheduler.registry.names())

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


async def test_read_child_rejects_shell_write_and_preserves_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = workspace / "original.txt"
    original.write_text("unchanged\n", encoding="utf-8")
    transport = RecordingTransport()
    factory = ChildRuntimeFactory(
        config=AppConfig(sandbox=SandboxConfig(enabled=False)),
        state_root=tmp_path / "state",
        parent_tools=create_builtin_registry(),
        model_chain=lambda _model: (ModelTarget("recording", "model", transport),),
    )

    async def publish(_event: ApprovalRequested) -> None:
        pass

    runtime = factory.create(
        record(1),
        workspace=workspace,
        parent_permission=PermissionMode.FULL_ACCESS,
        aggregate_budget=AggregateBudget(
            max_model_steps=10,
            max_tool_calls=10,
            max_runtime_seconds=60,
        ),
        approval_router=ApprovalRouter(
            parent_session_id="parent", parent_run_id="run", publish=publish
        ),
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
    assert direct.is_error
    assert direct.data["error"] == "network_disabled"


async def test_child_user_question_call_becomes_blocked(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = RecordingTransport()
    factory = ChildRuntimeFactory(
        config=AppConfig(),
        state_root=tmp_path / "state",
        parent_tools=create_builtin_registry(),
        model_chain=lambda _model: (ModelTarget("recording", "model", transport),),
    )

    async def publish(_event: ApprovalRequested) -> None:
        pass

    runtime = factory.create(
        record(1),
        workspace=workspace,
        parent_permission=PermissionMode.DEFAULT,
        aggregate_budget=AggregateBudget(
            max_model_steps=10,
            max_tool_calls=10,
            max_runtime_seconds=60,
        ),
        approval_router=ApprovalRouter(
            parent_session_id="parent", parent_run_id="run", publish=publish
        ),
    )
    assert "ask_user" not in runtime.loop.scheduler.registry.names()
    with pytest.raises(AgentBlocked, match="clarification is required"):
        await runtime.loop.scheduler.execute(
            (ScheduledCall("question", "ask_user", {"questions": []}),),
            ToolContext(workspace, "child-run", lambda: False),
        )
