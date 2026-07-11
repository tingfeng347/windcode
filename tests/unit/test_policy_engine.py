import pytest

from windcode.config import PermissionMode
from windcode.domain.tools import ToolEffect
from windcode.policy import PolicyAction, PolicyEngine, PolicyRequest, RiskLevel


def request(*effects: ToolEffect, command: str | None = None) -> PolicyRequest:
    return PolicyRequest(
        request_id="request",
        call_id="call",
        tool_name="tool",
        effects=frozenset(effects),
        summary="operation",
        command=command,
    )


@pytest.mark.parametrize(
    ("mode", "effects", "action"),
    [
        (PermissionMode.PLAN, (ToolEffect.READ,), PolicyAction.ALLOW),
        (PermissionMode.PLAN, (ToolEffect.WORKSPACE_WRITE,), PolicyAction.DENY),
        (PermissionMode.DEFAULT, (ToolEffect.READ,), PolicyAction.ALLOW),
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


def test_missing_sandbox_requires_approval_even_in_full_access() -> None:
    decision = PolicyEngine(
        PermissionMode.FULL_ACCESS, sandbox_enabled=True, sandbox_available=False
    ).evaluate(request(ToolEffect.PROCESS))
    assert decision.action is PolicyAction.ASK
    assert "unavailable" in decision.reason


def test_session_approval_reuses_matching_effect_set() -> None:
    engine = PolicyEngine(PermissionMode.DEFAULT)
    operation = request(ToolEffect.WORKSPACE_WRITE)
    engine.approve_for_session(operation)
    assert engine.evaluate(operation).action is PolicyAction.ALLOW
