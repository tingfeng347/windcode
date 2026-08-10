from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from windcode._fsync import fsync_directory
from windcode.extensions.models import Diagnostic, DiagnosticSeverity, DiagnosticStage


@dataclass(frozen=True, slots=True)
class InstalledPlugin:
    plugin_id: str
    version: str
    digest: str
    source_label: str
    installed_at: str
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class WorkspaceTrust:
    key: str
    canonical_path: str
    device: int
    inode: int
    trusted: bool


@dataclass(frozen=True, slots=True)
class ManagementAuditRecord:
    event_id: str
    action: str
    generation: int
    source_id: str
    status: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class ExtensionState:
    version: int = 1
    plugins: dict[str, InstalledPlugin] = field(default_factory=dict[str, InstalledPlugin])
    workspaces: dict[str, WorkspaceTrust] = field(default_factory=dict[str, WorkspaceTrust])
    enabled: dict[str, bool] = field(default_factory=dict[str, bool])
    global_capability_trust: dict[str, bool] = field(default_factory=dict[str, bool])
    capability_trust: dict[str, dict[str, bool]] = field(default_factory=dict[str, dict[str, bool]])
    config: dict[str, dict[str, str | int | float | bool]] = field(
        default_factory=dict[str, dict[str, str | int | float | bool]]
    )
    audit: tuple[ManagementAuditRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class StateLoadResult:
    state: ExtensionState | None
    diagnostics: tuple[Diagnostic, ...] = ()


def workspace_identity(workspace: Path) -> WorkspaceTrust:
    canonical = workspace.expanduser().resolve(strict=True)
    info = canonical.stat()
    raw = f"{canonical}\0{info.st_dev}\0{info.st_ino}".encode()
    key = hashlib.sha256(raw).hexdigest()
    return WorkspaceTrust(key, str(canonical), info.st_dev, info.st_ino, trusted=False)


class ExtensionStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()

    def load(self) -> StateLoadResult:
        if not self.path.exists():
            return StateLoadResult(ExtensionState())
        try:
            raw = cast(dict[str, Any], json.loads(self.path.read_text(encoding="utf-8")))
            plugins = {
                key: InstalledPlugin(**cast(dict[str, Any], value))
                for key, value in cast(dict[str, object], raw.get("plugins", {})).items()
            }
            workspaces = {
                key: WorkspaceTrust(**cast(dict[str, Any], value))
                for key, value in cast(dict[str, object], raw.get("workspaces", {})).items()
            }
            state = ExtensionState(
                version=int(raw.get("version", 1)),
                plugins=plugins,
                workspaces=workspaces,
                enabled={
                    str(key): bool(value)
                    for key, value in cast(dict[str, object], raw.get("enabled", {})).items()
                },
                global_capability_trust={
                    str(capability_id): bool(trusted)
                    for capability_id, trusted in cast(
                        dict[str, object], raw.get("global_capability_trust", {})
                    ).items()
                },
                capability_trust={
                    str(workspace_key): {
                        str(capability_id): bool(trusted)
                        for capability_id, trusted in cast(dict[str, object], values).items()
                    }
                    for workspace_key, values in cast(
                        dict[str, object], raw.get("capability_trust", {})
                    ).items()
                },
                config=cast(dict[str, dict[str, str | int | float | bool]], raw.get("config", {})),
                audit=tuple(
                    ManagementAuditRecord(**cast(dict[str, Any], value))
                    for value in cast(list[object], raw.get("audit", []))
                ),
            )
            return StateLoadResult(state)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            diagnostic = Diagnostic(
                stage=DiagnosticStage.PARSE,
                severity=DiagnosticSeverity.ERROR,
                category="state_corrupt",
                message=f"extension state could not be read: {exc}",
                source_id="extension-state",
                suggestion="Repair or restore the state file; Windcode did not overwrite it.",
            )
            return StateLoadResult(None, (diagnostic,))

    def save(self, state: ExtensionState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        temporary = self.path.with_name(f".{self.path.name}.tmp-{uuid4().hex}")
        payload = json.dumps(asdict(state), sort_keys=True, separators=(",", ":")) + "\n"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
            fsync_directory(self.path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def set_workspace_trust(
        self, state: ExtensionState, workspace: Path, trusted: bool
    ) -> ExtensionState:
        identity = workspace_identity(workspace)
        record = WorkspaceTrust(
            identity.key,
            identity.canonical_path,
            identity.device,
            identity.inode,
            trusted,
        )
        workspaces = dict(state.workspaces)
        workspaces[record.key] = record
        capability_trust = dict(state.capability_trust)
        capability_trust.pop(record.key, None)
        return replace(
            state,
            workspaces=workspaces,
            capability_trust=capability_trust,
        )

    def is_workspace_trusted(self, state: ExtensionState, workspace: Path) -> bool:
        identity = workspace_identity(workspace)
        record = state.workspaces.get(identity.key)
        return record is not None and record.trusted

    def set_capability_trust(
        self,
        state: ExtensionState,
        workspace: Path,
        capability_id: str,
        trusted: bool,
    ) -> ExtensionState:
        workspace_key = workspace_identity(workspace).key
        capability_trust = {key: dict(values) for key, values in state.capability_trust.items()}
        capability_trust.setdefault(workspace_key, {})[capability_id] = trusted
        return replace(state, capability_trust=capability_trust)

    def is_capability_trusted(
        self,
        state: ExtensionState,
        workspace: Path,
        capability_id: str,
        *,
        default: bool,
    ) -> bool:
        workspace_key = workspace_identity(workspace).key
        return state.capability_trust.get(workspace_key, {}).get(capability_id, default)

    def set_global_capability_trust(
        self,
        state: ExtensionState,
        capability_id: str,
        trusted: bool,
    ) -> ExtensionState:
        values = dict(state.global_capability_trust)
        values[capability_id] = trusted
        return replace(state, global_capability_trust=values)

    @staticmethod
    def is_global_capability_trusted(
        state: ExtensionState,
        capability_id: str,
        *,
        default: bool,
    ) -> bool:
        return state.global_capability_trust.get(capability_id, default)
