"""Stable public types for embedding Windcode."""

from windcode.domain.events import (
    AgentEventType,
    ApprovalRequested,
    ApprovalResponse,
    RunRequest,
    RunResponse,
    RunResult,
    UserInputRequested,
    UserResponse,
)
from windcode.domain.tools import Tool, ToolContext, ToolEffect, ToolResult

AgentEvent = AgentEventType

__all__ = [
    "AgentEvent",
    "ApprovalRequested",
    "ApprovalResponse",
    "RunRequest",
    "RunResponse",
    "RunResult",
    "Tool",
    "ToolContext",
    "ToolEffect",
    "ToolResult",
    "UserInputRequested",
    "UserResponse",
]
