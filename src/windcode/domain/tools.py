from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel


class ToolEffect(StrEnum):
    READ = "read"
    WORKSPACE_WRITE = "workspace_write"
    PROCESS = "process"
    NETWORK = "network"
    OUTSIDE_WORKSPACE = "outside_workspace"
    USER_INTERACTION = "user_interaction"
    AGENT_COMMUNICATION = "agent_communication"


@dataclass(frozen=True, slots=True)
class ToolContext:
    workspace: Path
    run_id: str
    cancelled: Callable[[], bool]
    progress: Callable[[str], Awaitable[None]] | None = None
    request_user: Callable[[object], Awaitable[object]] | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    output: str
    is_error: bool = False
    artifact_ref: str | None = None
    elapsed_seconds: float = 0.0
    data: dict[str, Any] = field(default_factory=dict[str, Any])


class Tool(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def input_model(self) -> type[BaseModel]: ...

    @property
    def effects(self) -> frozenset[ToolEffect]: ...

    def execute(self, context: ToolContext, arguments: BaseModel) -> Awaitable[ToolResult]: ...


ValidatedArguments = BaseModel | Mapping[str, Any]
