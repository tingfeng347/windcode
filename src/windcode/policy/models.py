from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from windcode.config.models import PermissionMode
from windcode.domain.tools import ToolEffect


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
    DENY = "deny"


class PolicyRequest(PolicyModel):
    request_id: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    effects: frozenset[ToolEffect]
    summary: str = Field(min_length=1)
    path: str | None = None
    command: str | None = None


class PolicyDecision(PolicyModel):
    action: PolicyAction
    risk: RiskLevel
    reason: str
    choices: tuple[ApprovalChoice, ...] = ()


__all__ = [
    "ApprovalChoice",
    "PermissionMode",
    "PolicyAction",
    "PolicyDecision",
    "PolicyRequest",
    "RiskLevel",
]
