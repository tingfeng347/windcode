import asyncio
from collections.abc import Mapping
from pathlib import Path
from time import monotonic
from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

from windcode.config import PermissionMode
from windcode.domain import tools as domain_tools
from windcode.domain.tools import (
    ToolContext,
    ToolEffect,
    ToolResult,
)
from windcode.policy import (
    ApprovalChoice,
    CommandAnalysis,
    PolicyAction,
    PolicyDecision,
    PolicyEngine,
    PolicyRequest,
    analyze_bash,
)
from windcode.runtime import ScheduledCall, ToolScheduler
from windcode.runtime.scheduler import PolicyConstraints, ScheduledResult
from windcode.sandbox import SandboxCapabilities, SandboxPolicy, SandboxPreset, SandboxStatus
from windcode.tools import ToolRegistry
from windcode.tools.shell import ShellTool


class DelayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    delay: float = 0.05


class DelayTool:
    description = "Delay for a test."
    input_model = DelayInput

    def __init__(
        self, name: str, effects: frozenset[ToolEffect], timeline: list[tuple[str, str]]
    ) -> None:
        self.name = name
        self.effects = effects
        self.timeline = timeline

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(DelayInput, arguments)
        self.timeline.append(("start", parsed.label))
        await asyncio.sleep(parsed.delay)
        self.timeline.append(("end", parsed.label))
        return ToolResult(parsed.label)


class AnalyzedShellInput(BaseModel):
    command: str
    network: bool = False


class AnalyzedShellTool:
    name = "shell"
    description = "Shell policy test double."
    input_model = AnalyzedShellInput
    effects = frozenset({ToolEffect.PROCESS})
    sandbox_policy = SandboxPolicy()

    class Sandbox:
        status = SandboxStatus(
            True,
            Path("/sandbox"),
            backend="test-sandbox",
            capabilities=SandboxCapabilities(True, True, True),
        )

    sandbox = Sandbox()

    def __init__(self) -> None:
        self.executions = 0

    def effects_for(self, arguments: Mapping[str, Any]) -> frozenset[ToolEffect]:
        effects = set(self.effects)
        if arguments.get("network") is True:
            effects.add(ToolEffect.NETWORK)
        return frozenset(effects)

    def analyze(self, arguments: Mapping[str, Any]) -> CommandAnalysis:
        return analyze_bash(str(arguments.get("command", "")))

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context, arguments
        self.executions += 1
        return ToolResult("executed")


def setup_scheduler(
    tmp_path: Path, mode: PermissionMode = PermissionMode.FULL_ACCESS
) -> tuple[ToolScheduler, ToolContext, list[tuple[str, str]]]:
    timeline: list[tuple[str, str]] = []
    registry = ToolRegistry()
    registry.register(DelayTool("read", frozenset({ToolEffect.READ}), timeline))
    registry.register(DelayTool("write", frozenset({ToolEffect.WORKSPACE_WRITE}), timeline))
    return (
        ToolScheduler(registry, PolicyEngine(mode, sandbox_enabled=False)),
        ToolContext(tmp_path, "run", lambda: False),
        timeline,
    )


@pytest.mark.asyncio
async def test_full_access_runs_intentionally_unsandboxed_shell_without_prompt(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(
        ShellTool(
            sandbox=None,
            sandbox_policy=SandboxPolicy(SandboxPreset.DANGER_FULL_ACCESS),
            platform="posix",
            shell_executable="bash",
        )
    )
    requests: list[PolicyRequest] = []
    decisions: list[PolicyDecision] = []

    async def observe(
        _call: ScheduledCall,
        request: PolicyRequest,
        decision: PolicyDecision,
    ) -> None:
        requests.append(request)
        decisions.append(decision)

    scheduler = ToolScheduler(
        registry,
        PolicyEngine(PermissionMode.FULL_ACCESS, sandbox_enabled=False),
        permission_observer=observe,
    )
    (result,) = await scheduler.execute(
        (ScheduledCall("shell", "shell", {"command": "printf windcode"}),),
        ToolContext(tmp_path, "run", lambda: False),
    )

    assert not result.result.is_error
    assert result.result.output == "windcode"
    assert requests[0].effects == {ToolEffect.PROCESS}
    assert requests[0].escalation_reason is None
    assert decisions[0].action is PolicyAction.ALLOW


@pytest.mark.asyncio
async def test_consecutive_reads_run_concurrently_and_keep_result_order(tmp_path: Path) -> None:
    scheduler, context, _ = setup_scheduler(tmp_path)
    started = monotonic()
    results = await scheduler.execute(
        (
            ScheduledCall("one", "read", {"label": "one", "delay": 0.08}),
            ScheduledCall("two", "read", {"label": "two", "delay": 0.02}),
        ),
        context,
    )

    assert monotonic() - started < 0.13
    assert [item.call_id for item in results] == ["one", "two"]
    assert [item.result.output for item in results] == ["one", "two"]
    assert all(item.result.elapsed_seconds > 0 for item in results)


@pytest.mark.asyncio
async def test_writes_are_exclusive_and_ordered(tmp_path: Path) -> None:
    scheduler, context, timeline = setup_scheduler(tmp_path)
    await scheduler.execute(
        (
            ScheduledCall("one", "write", {"label": "one", "delay": 0.01}),
            ScheduledCall("two", "write", {"label": "two", "delay": 0.01}),
        ),
        context,
    )
    assert timeline == [("start", "one"), ("end", "one"), ("start", "two"), ("end", "two")]


@pytest.mark.asyncio
async def test_scheduler_uses_tool_origin_before_extension_policy(tmp_path: Path) -> None:
    timeline: list[tuple[str, str]] = []
    tool = DelayTool("plugin_tool", frozenset({ToolEffect.READ}), timeline)
    tool.origin = "plugin:review/mcp"  # type: ignore[attr-defined]
    registry = ToolRegistry()
    registry.register(tool)
    observed: list[str | None] = []

    async def before_policy(call: ScheduledCall, _context: ToolContext) -> PolicyConstraints:
        observed.append(call.origin)
        return PolicyConstraints(additional_effects=frozenset({ToolEffect.NETWORK}))

    requests: list[PolicyRequest] = []

    async def permission_observer(
        _call: ScheduledCall, request: PolicyRequest, _decision: PolicyDecision
    ) -> None:
        requests.append(request)

    scheduler = ToolScheduler(
        registry,
        PolicyEngine(PermissionMode.FULL_ACCESS),
        before_policy=before_policy,
        permission_observer=permission_observer,
    )
    await scheduler.execute(
        (ScheduledCall("one", "plugin_tool", {"label": "one", "delay": 0}),),
        ToolContext(tmp_path, "run", lambda: False),
    )

    assert observed == ["plugin:review/mcp"]
    assert requests[0].effects == {ToolEffect.READ, ToolEffect.NETWORK}


@pytest.mark.asyncio
async def test_denied_approval_has_no_side_effect(tmp_path: Path) -> None:
    timeline: list[tuple[str, str]] = []
    registry = ToolRegistry()
    registry.register(DelayTool("write", frozenset({ToolEffect.WORKSPACE_WRITE}), timeline))

    async def deny(_request: PolicyRequest, _decision: PolicyDecision) -> ApprovalChoice:
        return ApprovalChoice.DENY

    scheduler = ToolScheduler(registry, PolicyEngine(PermissionMode.DEFAULT), approval_handler=deny)
    results = await scheduler.execute(
        (ScheduledCall("one", "write", {"label": "one"}),),
        ToolContext(tmp_path, "run", lambda: False),
    )
    assert results[0].result.data["error"] == "approval_denied"
    assert timeline == []


@pytest.mark.asyncio
async def test_full_access_network_shell_skips_approval(tmp_path: Path) -> None:
    registry = ToolRegistry()
    shell = AnalyzedShellTool()
    registry.register(shell)

    async def unexpected(_request: PolicyRequest, _decision: PolicyDecision) -> ApprovalChoice:
        pytest.fail("full_access network shell unexpectedly requested approval")

    scheduler = ToolScheduler(
        registry,
        PolicyEngine(PermissionMode.FULL_ACCESS),
        approval_handler=unexpected,
    )
    results = await scheduler.execute(
        (ScheduledCall("one", "shell", {"command": "curl https://example.com", "network": True}),),
        ToolContext(tmp_path, "run", lambda: False),
    )

    assert results[0].result.output == "executed"
    assert shell.executions == 1


@pytest.mark.asyncio
async def test_unparsed_shell_only_offers_one_time_approval(tmp_path: Path) -> None:
    registry = ToolRegistry()
    shell = AnalyzedShellTool()
    registry.register(shell)
    observed: list[PolicyDecision] = []

    async def deny(_request: PolicyRequest, decision: PolicyDecision) -> ApprovalChoice:
        observed.append(decision)
        return ApprovalChoice.DENY

    scheduler = ToolScheduler(
        registry,
        PolicyEngine(PermissionMode.FULL_ACCESS),
        approval_handler=deny,
    )
    results = await scheduler.execute(
        (ScheduledCall("one", "shell", {"command": "echo '"}),),
        ToolContext(tmp_path, "run", lambda: False),
    )

    assert observed[0].action is PolicyAction.ASK
    assert observed[0].choices == (
        ApprovalChoice.ALLOW_ONCE,
        ApprovalChoice.DENY,
        ApprovalChoice.CANCEL,
    )
    assert results[0].result.data["error"] == "approval_denied"
    assert shell.executions == 0


def test_runtime_scheduler_preserves_domain_contract_identity() -> None:
    assert ScheduledCall is domain_tools.ScheduledCall
    assert ScheduledResult is domain_tools.ScheduledResult
    assert PolicyConstraints is domain_tools.PolicyConstraints
