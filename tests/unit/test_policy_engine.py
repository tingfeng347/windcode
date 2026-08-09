import pytest

from windcode.config import PermissionMode
from windcode.domain.tools import ToolEffect
from windcode.policy import (
    ApprovalChoice,
    CommandAnalysis,
    PolicyAction,
    PolicyEngine,
    PolicyRequest,
    RiskLevel,
    analyze_bash,
)


def request(
    *effects: ToolEffect,
    command: str | None = None,
    tool_name: str = "tool",
    network: bool = False,
    sandbox_backend: str | None = None,
    command_analysis: CommandAnalysis | None = None,
) -> PolicyRequest:
    return PolicyRequest(
        request_id="request",
        call_id="call",
        tool_name=tool_name,
        effects=frozenset(effects),
        summary="operation",
        command=command,
        network=network,
        sandbox_backend=sandbox_backend,
        command_analysis=command_analysis,
    )


@pytest.mark.parametrize(
    ("mode", "effects", "action"),
    [
        (PermissionMode.PLAN, (ToolEffect.READ,), PolicyAction.ALLOW),
        (PermissionMode.PLAN, (ToolEffect.AGENT_COMMUNICATION,), PolicyAction.ALLOW),
        (PermissionMode.PLAN, (ToolEffect.WORKSPACE_WRITE,), PolicyAction.DENY),
        (PermissionMode.DEFAULT, (ToolEffect.READ,), PolicyAction.ALLOW),
        (PermissionMode.DEFAULT, (ToolEffect.AGENT_COMMUNICATION,), PolicyAction.ALLOW),
        (PermissionMode.DEFAULT, (ToolEffect.PROCESS,), PolicyAction.ASK),
        (PermissionMode.ACCEPT_EDITS, (ToolEffect.WORKSPACE_WRITE,), PolicyAction.ALLOW),
        (PermissionMode.ACCEPT_EDITS, (ToolEffect.NETWORK,), PolicyAction.ASK),
        (PermissionMode.FULL_ACCESS, (ToolEffect.OUTSIDE_WORKSPACE,), PolicyAction.ALLOW),
    ],
)
def test_permission_mode_matrix(
    mode: PermissionMode,
    effects: tuple[ToolEffect, ...],
    action: PolicyAction,
) -> None:
    assert PolicyEngine(mode).evaluate(request(*effects)).action is action


def test_dangerous_command_is_critical() -> None:
    decision = PolicyEngine(PermissionMode.DEFAULT).evaluate(
        request(ToolEffect.PROCESS, command="rm -rf build")
    )
    assert decision.risk is RiskLevel.CRITICAL


@pytest.mark.parametrize(
    "command",
    (
        "Remove-Item .\\build -Recurse -Force",
        "Format-Volume -DriveLetter D",
        "Clear-Disk -Number 1",
        "Restart-Computer",
    ),
)
def test_dangerous_powershell_command_is_critical(command: str) -> None:
    decision = PolicyEngine(PermissionMode.DEFAULT).evaluate(
        request(ToolEffect.PROCESS, command=command)
    )
    assert decision.risk is RiskLevel.CRITICAL


def test_missing_sandbox_requires_approval_even_in_full_access() -> None:
    decision = PolicyEngine(
        PermissionMode.FULL_ACCESS, sandbox_enabled=True, sandbox_available=False
    ).evaluate(request(ToolEffect.PROCESS))
    assert decision.action is PolicyAction.ASK
    assert "unavailable" in decision.reason


def test_full_access_allows_shell_network_without_approval() -> None:
    analysis = analyze_bash("curl https://example.com")
    decision = PolicyEngine(PermissionMode.FULL_ACCESS).evaluate(
        request(
            ToolEffect.PROCESS,
            ToolEffect.NETWORK,
            command="curl https://example.com",
            tool_name="shell",
            network=True,
            sandbox_backend="bubblewrap",
            command_analysis=analysis,
        )
    )

    assert decision.action is PolicyAction.ALLOW


@pytest.mark.parametrize(
    "mode",
    (PermissionMode.DEFAULT, PermissionMode.ACCEPT_EDITS, PermissionMode.FULL_ACCESS),
)
def test_untrusted_shell_analysis_always_requires_one_time_approval(
    mode: PermissionMode,
) -> None:
    analysis = analyze_bash("echo '")
    assert not analysis.trusted
    decision = PolicyEngine(mode).evaluate(
        request(
            ToolEffect.PROCESS,
            command="echo '",
            tool_name="shell",
            sandbox_backend="bubblewrap",
            command_analysis=analysis,
        )
    )

    assert decision.action is PolicyAction.ASK
    assert decision.choices == (
        ApprovalChoice.ALLOW_ONCE,
        ApprovalChoice.DENY,
        ApprovalChoice.CANCEL,
    )
    assert "analysis failed" in decision.reason


def test_session_approval_reuses_matching_effect_set() -> None:
    engine = PolicyEngine(PermissionMode.DEFAULT)
    operation = request(ToolEffect.WORKSPACE_WRITE)
    engine.approve_for_session(operation)
    assert engine.evaluate(operation).action is PolicyAction.ALLOW


def test_permission_mode_can_change_during_run() -> None:
    engine = PolicyEngine(PermissionMode.PLAN)
    operation = request(ToolEffect.WORKSPACE_WRITE)
    assert engine.evaluate(operation).action is PolicyAction.DENY

    engine.set_mode(PermissionMode.ACCEPT_EDITS)
    assert engine.evaluate(operation).action is PolicyAction.ALLOW


def test_switching_to_plan_overrides_session_approval() -> None:
    engine = PolicyEngine(PermissionMode.DEFAULT)
    operation = request(ToolEffect.WORKSPACE_WRITE)
    engine.approve_for_session(operation)
    assert engine.evaluate(operation).action is PolicyAction.ALLOW

    engine.set_mode(PermissionMode.PLAN)
    assert engine.evaluate(operation).action is PolicyAction.DENY
