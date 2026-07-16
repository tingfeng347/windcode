from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class SandboxPreset(StrEnum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    DANGER_FULL_ACCESS = "danger_full_access"


class SandboxState(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


class SandboxPermissions(StrEnum):
    USE_DEFAULT = "use_default"
    REQUIRE_ESCALATED = "require_escalated"


@dataclass(frozen=True, slots=True)
class SandboxCapabilities:
    filesystem_isolation: bool
    network_isolation: bool
    process_isolation: bool


@dataclass(frozen=True, slots=True)
class SandboxStatus:
    available: bool
    executable: Path | None
    warning: str | None = None
    backend: str = "unknown"
    capabilities: SandboxCapabilities = SandboxCapabilities(False, False, False)
    remediation: str | None = None

    @property
    def state(self) -> SandboxState:
        return SandboxState.READY if self.available else SandboxState.UNAVAILABLE


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    preset: SandboxPreset = SandboxPreset.WORKSPACE_WRITE
    writable_roots: tuple[Path, ...] = ()
    network_enabled: bool = False


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    command: tuple[str, ...]
    cwd: Path
    environment: dict[str, str] | None = None
    backend: str = "none"
    sandboxed: bool = False
