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

    async def open(self, *, state_root: Path, workspace: Path) -> None:
        if self.configuration.current.memory.enabled:
            self.service = MemoryService(state_root, workspace)
            await self.service.migrate()

    def require(self) -> MemoryService:
        if not self.configuration.current.memory.enabled or self.service is None:
            raise RuntimeError("long-term memory is disabled")
        return self.service

    async def list(self, *, status: MemoryStatus | None = None) -> tuple[MemoryRecord, ...]:
        service = self.require()
        statuses = (status,) if status is not None else None
        return await service.list(statuses=statuses)

    async def search(self, query: str, *, limit: int | None = None) -> tuple[MemoryRecord, ...]:
        service = self.require()
        results = await service.search(
            query,
            limit=limit or self.configuration.current.memory.recall_limit,
            statuses=(MemoryStatus.ACTIVE, MemoryStatus.CANDIDATE),
        )
        return tuple(result.record for result in results)

    async def get(self, memory_id: str) -> MemoryRecord:
        return await self.require().get(memory_id)

    async def create_candidate(
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
        return await self.require().create_candidate(
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

    async def transition(self, memory_id: str, status: MemoryStatus) -> MemoryRecord:
        return await self.require().transition(memory_id, status)

    async def update(self, memory_id: str, **changes: Any) -> MemoryRecord:
        return await self.require().update(memory_id, **changes)

    async def set_activation(
        self, memory_id: str, activation: MemoryActivation | str
    ) -> MemoryRecord:
        value = (
            activation if isinstance(activation, MemoryActivation) else MemoryActivation(activation)
        )
        return await self.require().update(memory_id, activation=value)

    async def delete(self, memory_id: str) -> None:
        await self.require().delete(memory_id)

    async def rebuild_index(self) -> int:
        return await self.require().store.rebuild()

    async def export_project(self, destination: Path) -> tuple[Path, ...]:
        service = self.require()
        return await service.store.export_project(service.project_id, destination)

    async def draft_skill(self, memory_id: str) -> str:
        return await self.require().draft_skill(memory_id)

    async def set_enabled(
        self,
        enabled: bool,
        *,
        config_file: Path,
        state_root: Path,
        workspace: Path,
    ) -> None:
        self.configuration.set_memory_enabled(enabled, config_file=config_file)
        if enabled:
            self.service = MemoryService(state_root, workspace)
            await self.service.migrate()
        else:
            self.service = None
