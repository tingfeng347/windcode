from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from windcode.config.models import PermissionMode
from windcode.domain.tools import ToolEffect
from windcode.policy.commands import CommandAnalysis, CommandRule


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class ApprovalChoice(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    ALLOW_PROJECT = "allow_project"
    DENY = "deny"
    CANCEL = "cancel"


class PolicyRequest(PolicyModel):
    request_id: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    effects: frozenset[ToolEffect]
    summary: str = Field(min_length=1)
    path: str | None = None
    command: str | None = None
    cwd: str | None = None
    network: bool = False
    sandbox_backend: str | None = None
    sandbox_preset: str | None = None
    escalation_reason: str | None = None
    command_analysis: CommandAnalysis | None = None
    proposed_rule: CommandRule | None = None


class PolicyDecision(PolicyModel):
    action: PolicyAction
    risk: RiskLevel
    reason: str
    choices: tuple[ApprovalChoice, ...] = ()


def summarize_policy_arguments(request: PolicyRequest, *, limit: int = 200) -> str | None:
    value = request.command or request.path
    if value is None:
        return None
    summary = " ".join(value.split())
    if len(summary) <= limit:
        return summary
    return summary[: limit - 3].rstrip() + "..."


__all__ = [
    "ApprovalChoice",
    "PermissionMode",
    "PolicyAction",
    "PolicyDecision",
    "PolicyRequest",
    "RiskLevel",
    "summarize_policy_arguments",
]
