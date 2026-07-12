from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any


class ExtensionScope(StrEnum):
    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"
    RUN = "run"


SCOPE_RANK = {scope: rank for rank, scope in enumerate(ExtensionScope)}


class CapabilityKind(StrEnum):
    SKILL = "skill"
    MCP_SERVER = "mcp_server"
    MCP_TOOL = "mcp_tool"
    MCP_RESOURCE = "mcp_resource"
    MCP_PROMPT = "mcp_prompt"
    HOOK = "hook"
    COMMAND = "command"
    PLUGIN = "plugin"


class DiagnosticStage(StrEnum):
    DISCOVER = "discover"
    PARSE = "parse"
    VALIDATE = "validate"
    MERGE = "merge"
    INSTALL = "install"
    ACTIVATE = "activate"
    EXECUTE = "execute"
    CLOSE = "close"


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ActivationState(StrEnum):
    INACTIVE = "inactive"
    UNTRUSTED = "untrusted"
    AVAILABLE = "available"
    ACTIVE = "active"
    FAILED = "failed"


_ID_PART = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


def normalize_id(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "-")
    if not _ID_PART.fullmatch(normalized):
        raise ValueError(f"invalid extension identifier: {value!r}")
    return normalized


def capability_id(kind: CapabilityKind, public_name: str, *, plugin_id: str | None = None) -> str:
    name = normalize_id(public_name)
    if plugin_id is None:
        return f"{kind.value}:{name}"
    return f"plugin:{normalize_id(plugin_id)}/{kind.value}/{name}"


@dataclass(frozen=True, slots=True)
class ExtensionSource:
    scope: ExtensionScope
    path: Path | None = None
    plugin_id: str | None = None
    component_id: str | None = None
    digest: str | None = None

    @property
    def source_id(self) -> str:
        if self.plugin_id is not None and self.component_id is not None:
            return f"plugin:{normalize_id(self.plugin_id)}/{normalize_id(self.component_id)}"
        label = str(self.path) if self.path is not None else "builtin"
        return f"{self.scope.value}:{label}"


@dataclass(frozen=True, slots=True)
class PermissionRequirement:
    filesystem_read: bool = False
    filesystem_write: bool = False
    network: bool = False
    process: bool = False


@dataclass(frozen=True, slots=True)
class Diagnostic:
    stage: DiagnosticStage
    severity: DiagnosticSeverity
    category: str
    message: str
    source_id: str
    suggestion: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    capability_id: str
    public_name: str
    kind: CapabilityKind
    source: ExtensionSource
    enabled: bool = True
    trusted: bool = True
    required: bool = False
    activation: ActivationState = ActivationState.AVAILABLE
    permissions: PermissionRequirement = field(default_factory=PermissionRequirement)
    shadowed_by: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def sort_key(self) -> tuple[int, str, str, str]:
        return (
            SCOPE_RANK[self.source.scope],
            self.kind.value,
            self.public_name,
            self.source.source_id,
        )


@dataclass(frozen=True, slots=True)
class ExtensionSnapshot:
    generation: int
    config_fingerprint: str
    capabilities: tuple[CapabilityRecord, ...] = ()
    definitions: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.generation < 0:
            raise ValueError("snapshot generation cannot be negative")
        ordered = tuple(sorted(self.capabilities, key=lambda item: item.sort_key))
        object.__setattr__(self, "capabilities", ordered)
        object.__setattr__(self, "definitions", MappingProxyType(dict(self.definitions)))
        object.__setattr__(
            self,
            "diagnostics",
            tuple(
                sorted(
                    self.diagnostics,
                    key=lambda item: (
                        item.source_id,
                        item.stage.value,
                        item.severity.value,
                        item.category,
                    ),
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ManagementResult:
    changed: bool
    reload_required: bool
    diagnostics: tuple[Diagnostic, ...] = ()
