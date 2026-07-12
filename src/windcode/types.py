"""Stable public types for embedding Windcode."""

from windcode.domain.events import (
    AgentEventType,
    ApprovalRequested,
    ApprovalResponse,
    RunRequest,
    RunResponse,
    RunResult,
    SubagentBlocked,
    SubagentCancelled,
    SubagentCleanup,
    SubagentCompleted,
    SubagentConflict,
    SubagentFailed,
    SubagentIntegrated,
    SubagentProgress,
    SubagentQueued,
    SubagentStarted,
    UserInputRequested,
    UserResponse,
)
from windcode.domain.subagents import (
    SubagentRecord,
    SubagentResult,
    SubagentRole,
    SubagentStatus,
    SubagentTaskKind,
    SubagentTaskSpec,
    VerificationResult,
)
from windcode.domain.tools import Tool, ToolContext, ToolEffect, ToolResult
from windcode.extensions.models import (
    CapabilityRecord,
    Diagnostic,
    ExtensionSnapshot,
    ManagementResult,
)
from windcode.extensions.plugins.installer import InstallResult
from windcode.extensions.state import ManagementAuditRecord

AgentEvent = AgentEventType

__all__ = [
    "AgentEvent",
    "ApprovalRequested",
    "ApprovalResponse",
    "CapabilityRecord",
    "Diagnostic",
    "ExtensionSnapshot",
    "InstallResult",
    "ManagementAuditRecord",
    "ManagementResult",
    "RunRequest",
    "RunResponse",
    "RunResult",
    "SubagentBlocked",
    "SubagentCancelled",
    "SubagentCleanup",
    "SubagentCompleted",
    "SubagentConflict",
    "SubagentFailed",
    "SubagentIntegrated",
    "SubagentProgress",
    "SubagentQueued",
    "SubagentRecord",
    "SubagentResult",
    "SubagentRole",
    "SubagentStarted",
    "SubagentStatus",
    "SubagentTaskKind",
    "SubagentTaskSpec",
    "Tool",
    "ToolContext",
    "ToolEffect",
    "ToolResult",
    "UserInputRequested",
    "UserResponse",
    "VerificationResult",
]
