from __future__ import annotations

from pathlib import Path
from typing import Any

from windcode.application.configuration import ConfigurationApplication
from windcode.memory import (
    MemoryActivation,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryService,
    MemorySource,
    MemoryStatus,
)


class MemoryApplication:
    """Own memory availability, current-project access, and configuration transitions."""

    def __init__(self, configuration: ConfigurationApplication) -> None:
        self.configuration = configuration
        self.service: MemoryService | None = None

    def open(self, *, state_root: Path, workspace: Path) -> None:
        if self.configuration.current.memory.enabled:
            self.service = MemoryService(state_root, workspace)

    def require(self) -> MemoryService:
        if not self.configuration.current.memory.enabled or self.service is None:
            raise RuntimeError("long-term memory is disabled")
        return self.service

    def list(self, *, status: MemoryStatus | None = None) -> tuple[MemoryRecord, ...]:
        service = self.require()
        return service.store.list(status=status, project_id=service.project_id)

    def search(self, query: str, *, limit: int | None = None) -> tuple[MemoryRecord, ...]:
        service = self.require()
        results = service.store.search(
            query,
            project_id=service.project_id,
            limit=limit or self.configuration.current.memory.recall_limit,
            statuses=(MemoryStatus.ACTIVE, MemoryStatus.CANDIDATE),
        )
        return tuple(result.record for result in results)

    def get(self, memory_id: str) -> MemoryRecord:
        return self.require().store.get(memory_id)

    def create_candidate(
        self,
        *,
        kind: MemoryKind,
        scope: MemoryScope,
        title: str,
        summary: str,
        body: str,
        source: MemorySource | None = None,
        tags: tuple[str, ...] = (),
        evidence: tuple[str, ...] = (),
        confidence: float = 0.5,
        activation: MemoryActivation | None = None,
        priority: int | None = None,
    ) -> MemoryRecord:
        return self.require().create_candidate(
            kind=kind,
            scope=scope,
            title=title,
            summary=summary,
            body=body,
            source=source,
            tags=tags,
            evidence=evidence,
            confidence=confidence,
            activation=activation,
            priority=priority,
        )

    def transition(self, memory_id: str, status: MemoryStatus) -> MemoryRecord:
        return self.require().store.transition(memory_id, status)

    def update(self, memory_id: str, **changes: Any) -> MemoryRecord:
        return self.require().store.update(memory_id, **changes)

    def set_activation(self, memory_id: str, activation: MemoryActivation | str) -> MemoryRecord:
        value = (
            activation if isinstance(activation, MemoryActivation) else MemoryActivation(activation)
        )
        return self.require().store.update(memory_id, activation=value)

    def delete(self, memory_id: str) -> None:
        self.require().store.delete(memory_id)

    def rebuild_index(self) -> int:
        return self.require().store.rebuild()

    def export_project(self, destination: Path) -> tuple[Path, ...]:
        service = self.require()
        return service.store.export_project(service.project_id, destination)

    def draft_skill(self, memory_id: str) -> str:
        return self.require().draft_skill(memory_id)

    def set_enabled(
        self,
        enabled: bool,
        *,
        config_file: Path,
        state_root: Path,
        workspace: Path,
    ) -> None:
        self.configuration.set_memory_enabled(enabled, config_file=config_file)
        self.service = MemoryService(state_root, workspace) if enabled else None
