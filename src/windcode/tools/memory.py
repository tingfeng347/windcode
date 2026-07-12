from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.memory import MemoryKind, MemoryRecord, MemoryScope, MemoryService, MemoryStatus
from windcode.tools.registry import ToolRegistry

MemoryToolObserver = Callable[[str, dict[str, Any]], Awaitable[None]]


class MemorySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=5, ge=1, le=20)
    kind: MemoryKind | None = None
    scope: MemoryScope | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE


class MemoryListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=100)
    kind: MemoryKind | None = None
    scope: MemoryScope | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE


class MemoryGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, max_length=64)


def _record_data(record: MemoryRecord, *, include_body: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "memory_id": record.memory_id,
        "kind": record.kind.value,
        "scope": record.scope.value,
        "status": record.status.value,
        "title": record.title,
        "summary": record.summary,
        "tags": list(record.tags),
        "confidence": record.confidence,
        "updated_at": record.updated_at.isoformat(),
        "evidence": list(record.evidence),
    }
    if include_body:
        data["body"] = record.body
    return data


def _matches(
    record: MemoryRecord,
    *,
    kind: MemoryKind | None,
    scope: MemoryScope | None,
) -> bool:
    return (kind is None or record.kind is kind) and (scope is None or record.scope is scope)


def _bounded(items: list[dict[str, Any]], max_chars: int) -> tuple[list[dict[str, Any]], bool]:
    selected: list[dict[str, Any]] = []
    size = 2
    for item in items:
        encoded = json.dumps(item, ensure_ascii=False)
        if selected and size + len(encoded) + 1 > max_chars:
            return selected, True
        if not selected and len(encoded) + 2 > max_chars:
            compact = dict(item)
            compact.pop("body", None)
            compact["truncated"] = True
            return [compact], True
        selected.append(item)
        size += len(encoded) + 1
    return selected, False


class _MemoryTool:
    effects = frozenset({ToolEffect.READ})

    def __init__(
        self,
        service: MemoryService,
        observer: MemoryToolObserver,
        *,
        max_chars: int,
    ) -> None:
        self.service = service
        self.observer = observer
        self.max_chars = max_chars

    async def _observe(self, action: str, details: dict[str, Any]) -> None:
        await self.observer(action, details)


class MemorySearchTool(_MemoryTool):
    name = "memory_search"
    description = (
        "Search visible long-term memories when the user explicitly asks what is remembered "
        "or requests memory lookup. Use this instead of searching workspace files."
    )
    input_model = MemorySearchInput

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(MemorySearchInput, arguments)
        candidates = self.service.store.search(
            parsed.query,
            project_id=self.service.project_id,
            limit=min(100, max(parsed.limit * 4, 20)),
            statuses=(parsed.status,),
            kind=parsed.kind,
            scope=parsed.scope,
        )
        records = [result.record for result in candidates][: parsed.limit]
        raw_items = [_record_data(record, include_body=True) for record in records]
        items, truncated = _bounded(raw_items, self.max_chars)
        data = {
            "query": parsed.query,
            "count": len(items),
            "truncated": truncated,
            "memories": items,
        }
        await self._observe(
            "searched",
            {
                "query": parsed.query,
                "count": len(items),
                "status": parsed.status.value,
            },
        )
        return ToolResult(json.dumps(data, ensure_ascii=False), data=data)


class MemoryListTool(_MemoryTool):
    name = "memory_list"
    description = (
        "List visible long-term memories for broad requests such as 'show what you remember'. "
        "Use filters instead of searching workspace files."
    )
    input_model = MemoryListInput

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(MemoryListInput, arguments)
        records = [
            record
            for record in self.service.store.list(
                status=parsed.status,
                project_id=self.service.project_id,
            )
            if _matches(record, kind=parsed.kind, scope=parsed.scope)
        ][: parsed.limit]
        raw_items = [_record_data(record, include_body=False) for record in records]
        items, truncated = _bounded(raw_items, self.max_chars)
        data = {"count": len(items), "truncated": truncated, "memories": items}
        await self._observe(
            "listed",
            {"count": len(items), "status": parsed.status.value},
        )
        return ToolResult(json.dumps(data, ensure_ascii=False), data=data)


class MemoryGetTool(_MemoryTool):
    name = "memory_get"
    description = (
        "Read one visible long-term memory by its exact ID or unique ID prefix. "
        "Never read memory Markdown files directly."
    )
    input_model = MemoryGetInput

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(MemoryGetInput, arguments)
        matches = tuple(
            record
            for record in self.service.store.list(project_id=self.service.project_id)
            if record.memory_id.startswith(parsed.memory_id)
        )
        if len(matches) != 1:
            return ToolResult(
                "memory ID does not exist or prefix is not unique",
                is_error=True,
                data={"error": "memory_not_found_or_ambiguous"},
            )
        raw = _record_data(matches[0], include_body=True)
        items, truncated = _bounded([raw], self.max_chars)
        data = {"memory": items[0], "truncated": truncated}
        await self._observe(
            "retrieved",
            {"memory_id": matches[0].memory_id, "status": matches[0].status.value},
        )
        return ToolResult(json.dumps(data, ensure_ascii=False), data=data)


def register_memory_tools(
    registry: ToolRegistry,
    service: MemoryService,
    observer: MemoryToolObserver,
    *,
    max_chars: int,
) -> None:
    for tool in (
        MemorySearchTool(service, observer, max_chars=max_chars),
        MemoryListTool(service, observer, max_chars=max_chars),
        MemoryGetTool(service, observer, max_chars=max_chars),
    ):
        registry.register(tool)
