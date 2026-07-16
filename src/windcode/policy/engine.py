from __future__ import annotations

import re

from windcode.config.models import PermissionMode
from windcode.domain.tools import ToolEffect
from windcode.policy.commands import CommandRule
from windcode.policy.models import (
    ApprovalChoice,
    PolicyAction,
    PolicyDecision,
    PolicyRequest,
    RiskLevel,
)
from windcode.policy.rules import CommandRuleStore

_LEGACY_CRITICAL = re.compile(
    r"(?:^|[;&|]\s*)(?:rm\s+-[^\n]*r[^\n]*f|mkfs\b|shutdown\b|reboot\b|"
    r"remove-item\b[^\n]*(?:-recurse[^\n]*-force|-force[^\n]*-recurse)|"
    r"format-volume\b|clear-disk\b|stop-computer\b|restart-computer\b)",
    re.IGNORECASE,
)


def assess_risk(request: PolicyRequest) -> RiskLevel:
    if ToolEffect.OUTSIDE_WORKSPACE in request.effects:
        return RiskLevel.CRITICAL
    if request.command_analysis is not None and request.command_analysis.critical:
        return RiskLevel.CRITICAL
    if (
        request.command_analysis is None
        and request.command
        and _LEGACY_CRITICAL.search(request.command)
    ):
        return RiskLevel.CRITICAL
    if ToolEffect.NETWORK in request.effects:
        return RiskLevel.HIGH
    if ToolEffect.PROCESS in request.effects:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _approval(
    reason: str,
    risk: RiskLevel,
    *,
    reusable: bool = False,
    critical: bool = False,
) -> PolicyDecision:
    choices = [ApprovalChoice.ALLOW_ONCE]
    if reusable and not critical:
        choices.extend((ApprovalChoice.ALLOW_SESSION, ApprovalChoice.ALLOW_PROJECT))
    choices.extend((ApprovalChoice.DENY, ApprovalChoice.CANCEL))
    return PolicyDecision(
        action=PolicyAction.ASK,
        risk=risk,
        reason=reason,
        choices=tuple(choices),
    )


class PolicyEngine:
    def __init__(
        self,
        mode: PermissionMode,
        *,
        sandbox_enabled: bool = True,
        sandbox_available: bool = True,
        rule_store: CommandRuleStore | None = None,
    ) -> None:
        self.mode = mode
        self.sandbox_enabled = sandbox_enabled
        self.sandbox_available = sandbox_available
        self.rule_store = rule_store
        self._session_approvals: set[tuple[str, frozenset[ToolEffect]]] = set()
        self._session_rules: list[CommandRule] = []

    @staticmethod
    def _fingerprint(request: PolicyRequest) -> tuple[str, frozenset[ToolEffect]]:
        return request.tool_name, request.effects

    def approve_for_session(self, request: PolicyRequest) -> None:
        if request.proposed_rule is not None:
            self._session_rules.append(
                request.proposed_rule.model_copy(update={"source": "session"})
            )
        else:
            self._session_approvals.add(self._fingerprint(request))

    def approve_for_project(self, request: PolicyRequest) -> None:
        if request.proposed_rule is None or self.rule_store is None:
            raise ValueError("this approval cannot be persisted as a project rule")
        self.rule_store.append(request.proposed_rule.model_copy(update={"source": "project"}))

    def restore_session_approval(
        self,
        tool_name: str,
        effects: frozenset[ToolEffect],
    ) -> None:
        self._session_approvals.add((tool_name, effects))

    def restore_session_rule(self, rule: CommandRule) -> None:
        self._session_rules.append(rule.model_copy(update={"source": "session"}))

    def set_mode(self, mode: PermissionMode) -> None:
        self.mode = mode

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        risk = assess_risk(request)
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

        analysis = request.command_analysis
        critical = (analysis is not None and analysis.critical) or (
            analysis is None
            and request.command is not None
            and _LEGACY_CRITICAL.search(request.command) is not None
        )
        reusable = request.proposed_rule is not None
        if critical:
            return _approval(
                "critical commands always require one-time approval",
                RiskLevel.CRITICAL,
                critical=True,
            )

        if analysis is not None and not analysis.trusted:
            detail = analysis.error or "unknown parser failure"
            return _approval(
                f"command analysis failed; one-time approval is required: {detail}",
                risk,
            )

        if analysis is not None and analysis.trusted and analysis.actions:
            rules = tuple(self._session_rules)
            if self.rule_store is not None:
                rules = (*rules, *self.rule_store.load())
            if all(
                any(
                    rule.matches(
                        action,
                        dialect=analysis.dialect,
                        network=request.network,
                        escalated=ToolEffect.OUTSIDE_WORKSPACE in effects,
                    )
                    for rule in rules
                )
                for action in analysis.actions
            ):
                return PolicyDecision(
                    action=PolicyAction.ALLOW,
                    risk=risk,
                    reason="all parsed command actions match approved rules",
                )

        if self._fingerprint(request) in self._session_approvals:
            return PolicyDecision(
                action=PolicyAction.ALLOW,
                risk=risk,
                reason="matching operation was approved for this session",
            )

        if ToolEffect.OUTSIDE_WORKSPACE in effects:
            if (
                self.mode is PermissionMode.FULL_ACCESS
                and request.escalation_reason is None
                and request.sandbox_backend is None
            ):
                return PolicyDecision(
                    action=PolicyAction.ALLOW,
                    risk=risk,
                    reason="full_access mode was explicitly selected",
                )
            return _approval(
                request.escalation_reason
                or "execution outside the system sandbox requires explicit approval",
                risk,
                reusable=reusable,
            )
        if ToolEffect.NETWORK in effects and request.tool_name == "shell":
            if self.mode is PermissionMode.FULL_ACCESS:
                return PolicyDecision(
                    action=PolicyAction.ALLOW,
                    risk=risk,
                    reason="full_access mode permits network access",
                )
            return _approval("network access requires explicit approval", risk, reusable=reusable)

        if ToolEffect.PROCESS in effects and request.tool_name == "shell":
            if (
                self.sandbox_enabled
                and self.sandbox_available
                and request.sandbox_backend is not None
            ):
                return PolicyDecision(
                    action=PolicyAction.ALLOW,
                    risk=risk,
                    reason="ordinary command is confined by the active system sandbox",
                )
            return _approval(
                "system sandbox is unavailable; explicit approval is required",
                risk,
                reusable=reusable,
            )

        if (
            ToolEffect.PROCESS in effects
            and self.sandbox_enabled
            and not self.sandbox_available
            and request.tool_name == "tool"
        ):
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
