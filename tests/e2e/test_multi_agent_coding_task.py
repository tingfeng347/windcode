from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from tests.run_builder_support import child_preparer
from windcode.config import AppConfig, PermissionMode, SandboxConfig
from windcode.domain.events import ApprovalRequested, ApprovalResponse
from windcode.domain.messages import Role, TextBlock
from windcode.domain.models import (
    ModelCompleted,
    ModelEvent,
    ModelRequest,
    ModelUsage,
    StopReason,
    TextDelta,
    ToolCallDelta,
    Usage,
)
from windcode.domain.subagents import (
    SubagentRole,
    SubagentStatus,
    SubagentTaskKind,
    SubagentTaskSpec,
)
from windcode.observability import TraceStore
from windcode.providers import ModelTarget
from windcode.runtime.event_bus import EventBus
from windcode.runtime.subagents.coordinator import SubagentCoordinator
from windcode.runtime.subagents.verification import VerificationRunner
from windcode.sessions import SessionStore
from windcode.tools import create_builtin_registry
from windcode.worktrees import WorktreeManager


def git(cwd: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments), cwd=cwd, text=True, capture_output=True, check=True
    )
    return completed.stdout.strip()


def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Windcode E2E")
    git(repo, "config", "user.email", "windcode@example.test")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-m", "initial")
    return repo


class MultiAgentTransport:
    name = "multi-agent-e2e"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        task_block = next(
            block
            for message in request.messages
            if message.role is Role.USER
            for block in message.content
            if isinstance(block, TextBlock) and block.text.startswith("Task:")
        )
        match = re.search(r"^Task: ([a-z0-9_]+)$", task_block.text, re.MULTILINE)
        assert match is not None
        task_name = match.group(1)
        usage = Usage(input_tokens=10, output_tokens=2)
        yield ModelUsage(usage)

        if task_name == "inspect_base":
            yield TextDelta("base.txt contains the expected baseline")
            yield ModelCompleted(StopReason.STOP, usage=usage)
            return
        if request.messages[-1].role is Role.USER:
            if os.name == "nt":
                command = (
                    f"Set-Content -NoNewline -Encoding utf8 {task_name}.txt "
                    f'"{task_name} completed`n"; '
                    f"git add {task_name}.txt; git commit -m '{task_name}'"
                )
            else:
                command = (
                    f"printf '{task_name} completed\\n' > {task_name}.txt && "
                    f"git add {task_name}.txt && git commit -m '{task_name}'"
                )
            yield ToolCallDelta(f"commit-{task_name}", "shell", json.dumps({"command": command}))
            yield ModelCompleted(StopReason.TOOL_USE, usage=usage)
            return
        yield TextDelta(f"{task_name} implemented and committed")
        yield ModelCompleted(StopReason.STOP, usage=usage)

    async def aclose(self) -> None:
        pass


def task(name: str, kind: SubagentTaskKind) -> SubagentTaskSpec:
    role = SubagentRole.RESEARCHER if kind is SubagentTaskKind.READ else SubagentRole.WORKER
    return SubagentTaskSpec(
        task_name=name,
        role=role,
        kind=kind,
        goal=f"complete {name}",
        context="Use only the assigned workspace and return concrete evidence.",
        expected_output="A concise report and, for write tasks, one clean commit.",
        verification=("verify the assigned result",),
    )


async def test_parallel_children_commit_then_integrate_in_order(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    initial_head = git(repo, "rev-parse", "HEAD")
    state = tmp_path / "state"
    parent_session = SessionStore.create(state / "sessions", "parent")
    parent_bus = EventBus(parent_session, TraceStore("parent-run", root=state / "traces"))
    transport = MultiAgentTransport()
    config = AppConfig(sandbox=SandboxConfig(preset="danger_full_access"))
    prepare_child = child_preparer(
        config=config,
        state_root=state,
        parent_tools=create_builtin_registry(),
        model_chain=lambda _model: (ModelTarget("e2e", "model", transport),),
    )
    coordinator = SubagentCoordinator(
        parent_session_id="parent",
        parent_run_id="parent-run",
        workspace=repo,
        permission_mode=PermissionMode.FULL_ACCESS,
        config=config.subagents,
        event_bus=parent_bus,
        prepare_child=prepare_child,
        worktrees=WorktreeManager(worktrees_root=tmp_path / "worktrees"),
        verification=VerificationRunner(),
    )

    async def approve_shell_commands() -> None:
        async for event in parent_bus.subscribe():
            if isinstance(event, ApprovalRequested):
                coordinator.approvals.respond(ApprovalResponse(event.request_id, "allow_once"))

    approver = asyncio.create_task(approve_shell_commands())
    specs = (
        task("inspect_base", SubagentTaskKind.READ),
        task("add_alpha", SubagentTaskKind.WRITE),
        task("add_beta", SubagentTaskKind.WRITE),
    )

    try:
        records = await coordinator.spawn(specs)
        async with asyncio.timeout(30):
            results = tuple([await coordinator.wait(record.subagent_id) for record in records])
    except TimeoutError as exc:
        states = ", ".join(
            f"{record.spec.task_name}={record.status.value}" for record in coordinator.list()
        )
        raise AssertionError(f"subagents did not finish within 30 seconds: {states}") from exc
    finally:
        approver.cancel()
        await asyncio.gather(approver, return_exceptions=True)
    assert [result.task_name for result in results] == [spec.task_name for spec in specs]
    assert all(result.status is SubagentStatus.COMPLETED for result in results)
    assert all(result.usage.input_tokens > 0 for result in results)

    snapshots = coordinator.list()
    write_records = snapshots[1:]
    worktrees = tuple(record.worktree_path for record in write_records)
    assert all(path is not None and path.exists() for path in worktrees)
    assert len(set(worktrees)) == 2
    assert all(record.commit for record in write_records)
    assert git(repo, "rev-parse", "HEAD") == initial_head
    assert git(repo, "status", "--porcelain") == ""
    assert not (repo / "add_alpha.txt").exists()
    assert not (repo / "add_beta.txt").exists()

    for record in write_records:
        verification_command = (
            f"if (-not (Test-Path {record.spec.task_name}.txt)) {{ exit 1 }}"
            if os.name == "nt"
            else f"test -f {record.spec.task_name}.txt"
        )
        integrated = await coordinator.integrate(
            record.subagent_id,
            (verification_command,),
        )
        assert integrated.status is SubagentStatus.INTEGRATED
        assert integrated.verification[0].passed

    assert (repo / "add_alpha.txt").read_text(encoding="utf-8") == "add_alpha completed\n"
    assert (repo / "add_beta.txt").read_text(encoding="utf-8") == "add_beta completed\n"
    assert all(path is not None and not path.exists() for path in worktrees)
    assert git(repo, "status", "--porcelain") == ""

    stored = parent_session.load_records()
    event_kinds = {
        str(record.payload.get("kind")) for record in stored if record.record_type == "agent_event"
    }
    assert {"subagent_queued", "subagent_started", "subagent_completed"} <= event_kinds
    assert {"subagent_integrated", "subagent_cleanup"} <= event_kinds
    assert sum(record.record_type == "subagent_result" for record in stored) >= 5
