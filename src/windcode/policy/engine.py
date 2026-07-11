from __future__ import annotations

import re

from windcode.config.models import PermissionMode
from windcode.domain.tools import ToolEffect
from windcode.policy.models import (
    ApprovalChoice,
    PolicyAction,
    PolicyDecision,
    PolicyRequest,
    RiskLevel,
)

_DANGEROUS_COMMAND = re.compile(
    r"(?:^|[;&|]\s*)(?:sudo\b|rm\s+-[^\n]*r[^\n]*f|mkfs\b|dd\s+if=|shutdown\b|reboot\b)"
)


def assess_risk(request: PolicyRequest) -> RiskLevel:
    if ToolEffect.OUTSIDE_WORKSPACE in request.effects:
        return RiskLevel.CRITICAL
    if request.command and _DANGEROUS_COMMAND.search(request.command):
        return RiskLevel.CRITICAL
    if ToolEffect.NETWORK in request.effects:
        return RiskLevel.HIGH
    if ToolEffect.PROCESS in request.effects:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _approval(reason: str, risk: RiskLevel) -> PolicyDecision:
    return PolicyDecision(
        action=PolicyAction.ASK,
        risk=risk,
        reason=reason,
        choices=(
            ApprovalChoice.ALLOW_ONCE,
            ApprovalChoice.ALLOW_SESSION,
            ApprovalChoice.DENY,
        ),
    )


class PolicyEngine:
    def __init__(
        self,
        mode: PermissionMode,
        *,
        sandbox_enabled: bool = True,
        sandbox_available: bool = True,
    ) -> None:
        self.mode = mode
        self.sandbox_enabled = sandbox_enabled
        self.sandbox_available = sandbox_available
        self._session_approvals: set[tuple[str, frozenset[ToolEffect]]] = set()

    @staticmethod
    def _fingerprint(request: PolicyRequest) -> tuple[str, frozenset[ToolEffect]]:
        return request.tool_name, request.effects

    def approve_for_session(self, request: PolicyRequest) -> None:
        self._session_approvals.add(self._fingerprint(request))

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        risk = assess_risk(request)
        if self._fingerprint(request) in self._session_approvals:
            return PolicyDecision(
                action=PolicyAction.ALLOW,
                risk=risk,
                reason="matching operation was approved for this session",
            )

        effects = request.effects
        side_effects = {
            ToolEffect.WORKSPACE_WRITE,
            ToolEffect.PROCESS,
            ToolEffect.NETWORK,
            ToolEffect.OUTSIDE_WORKSPACE,
        }
        if self.mode is PermissionMode.PLAN:
            if effects & side_effects:
                return PolicyDecision(
                    action=PolicyAction.DENY,
                    risk=risk,
                    reason="plan mode does not permit side effects",
                )
            return PolicyDecision(
                action=PolicyAction.ALLOW,
                risk=risk,
                reason="plan mode permits reading and user interaction",
            )

        sandbox_degraded = (
            ToolEffect.PROCESS in effects and self.sandbox_enabled and not self.sandbox_available
        )
        if sandbox_degraded:
            return _approval("system sandbox is unavailable; explicit approval is required", risk)

        if self.mode is PermissionMode.DEFAULT:
            if effects & side_effects:
                return _approval("default mode requires approval for side effects", risk)
            return PolicyDecision(
                action=PolicyAction.ALLOW,
                risk=risk,
                reason="read-only operation is allowed",
            )

        if self.mode is PermissionMode.ACCEPT_EDITS:
            disallowed = effects & {
                ToolEffect.PROCESS,
                ToolEffect.NETWORK,
                ToolEffect.OUTSIDE_WORKSPACE,
            }
            if disallowed or risk is RiskLevel.CRITICAL:
                return _approval("operation exceeds automatic workspace edit permission", risk)
            return PolicyDecision(
                action=PolicyAction.ALLOW,
                risk=risk,
                reason="workspace edits are allowed in accept_edits mode",
            )

        return PolicyDecision(
            action=PolicyAction.ALLOW,
            risk=risk,
            reason="full_access mode was explicitly selected",
        )
