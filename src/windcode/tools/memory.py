from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.memory import (
    MemoryActivation,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryService,
    MemorySource,
    MemoryStatus,
    classify_memory_intent,
    explicitly_always_project_fact,
    has_explicit_memory_intent,
)
from windcode.memory.refiner import (
    RefinedMemory,
    assess_core_project_fact,
    assess_experience,
    refine_memory,
)
from windcode.memory.security import SensitiveMemoryError, validate_memory_text
from windcode.providers import ModelTarget
from windcode.tools.registry import ToolRegistry

MemoryToolObserver = Callable[[str, dict[str, Any]], Awaitable[None]]


class MemorySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=5, ge=1, le=20)
    kind: MemoryKind | None = None
    scope: MemoryScope | None = None
    status: MemoryStatus | None = None
    activation: MemoryActivation | None = None


class MemoryListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=100)
    kind: MemoryKind | None = None
    scope: MemoryScope | None = None
    status: MemoryStatus | None = None
    activation: MemoryActivation | None = None


class MemoryGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, max_length=64)


class MemoryWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4_000)
    kind: MemoryKind | None = None
    scope: MemoryScope | None = None


class MemoryUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=4_000)


class MemoryDeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, max_length=64)


def _record_data(record: MemoryRecord, *, include_body: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "memory_id": record.memory_id,
        "kind": record.kind.value,
        "scope": record.scope.value,
        "status": record.status.value,
        "activation": record.activation.value,
        "priority": record.priority,
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
    activation: MemoryActivation | None,
) -> bool:
    return (
        (kind is None or record.kind is kind)
        and (scope is None or record.scope is scope)
        and (activation is None or record.activation is activation)
    )


def _query_statuses(status: MemoryStatus | None) -> tuple[MemoryStatus, ...]:
    return (MemoryStatus.ACTIVE, MemoryStatus.CANDIDATE) if status is None else (status,)


def _status_label(status: MemoryStatus | None) -> str:
    return "active,candidate" if status is None else status.value


def _bounded(items: list[dict[str, Any]], max_chars: int) -> tuple[list[dict[str, Any]], bool]:
    selected: list[dict[str, Any]] = []
    size = 2  # JSON array brackets "[]"
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
        "or requests memory lookup. By default include active and candidate records and report "
        "their status accurately. Use this instead of searching workspace files."
    )
    input_model = MemorySearchInput

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(MemorySearchInput, arguments)
        candidates = await self.service.search(
            parsed.query,
            limit=min(100, max(parsed.limit * 4, 20)),
            statuses=_query_statuses(parsed.status),
            kind=parsed.kind,
            scope=parsed.scope,
            activation=parsed.activation,
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
                "status": _status_label(parsed.status),
            },
        )
        return ToolResult(json.dumps(data, ensure_ascii=False), data=data)


class MemoryListTool(_MemoryTool):
    name = "memory_list"
    description = (
        "List visible long-term memories for broad requests such as 'show what you remember'. "
        "By default include active and candidate records and report their status accurately. "
        "Use filters instead of searching workspace files."
    )
    input_model = MemoryListInput

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(MemoryListInput, arguments)
        statuses = _query_statuses(parsed.status)
        records = [
            record
            for record in await self.service.list(
                statuses=statuses,
                activation=parsed.activation,
            )
            if _matches(
                record,
                kind=parsed.kind,
                scope=parsed.scope,
                activation=parsed.activation,
            )
        ][: parsed.limit]
        raw_items = [_record_data(record, include_body=False) for record in records]
        items, truncated = _bounded(raw_items, self.max_chars)
        data = {"count": len(items), "truncated": truncated, "memories": items}
        await self._observe(
            "listed",
            {"count": len(items), "status": _status_label(parsed.status)},
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
        try:
            match = await self.service.resolve_prefix(parsed.memory_id)
        except ValueError:
            return ToolResult(
                "memory ID does not exist or prefix is not unique",
                is_error=True,
                data={"error": "memory_not_found_or_ambiguous"},
            )
        raw = _record_data(match, include_body=True)
        items, truncated = _bounded([raw], self.max_chars)
        data = {"memory": items[0], "truncated": truncated}
        await self._observe(
            "retrieved",
            {"memory_id": match.memory_id, "status": match.status.value},
        )
        return ToolResult(json.dumps(data, ensure_ascii=False), data=data)


class MemoryWriteTool(_MemoryTool):
    name = "memory_write"
    description = (
        "Write a long-term memory when the user explicitly asks, or autonomously when a completed "
        "task produced a durable, reusable engineering experience. Autonomous writes must use "
        "kind=experience; do not save routine results, temporary state, guesses, or raw tool "
        "output. "
        "Never claim it was saved before this tool succeeds."
    )
    input_model = MemoryWriteInput
    effects = frozenset({ToolEffect.OUTSIDE_WORKSPACE})

    def __init__(
        self,
        service: MemoryService,
        observer: MemoryToolObserver,
        *,
        max_chars: int,
        user_prompt: str,
        source: MemorySource,
        model: ModelTarget,
        enabled_kinds: frozenset[MemoryKind] | None = None,
    ) -> None:
        super().__init__(service, observer, max_chars=max_chars)
        self.user_prompt = user_prompt
        self.source = source
        self.model = model
        self.enabled_kinds = frozenset(MemoryKind) if enabled_kinds is None else enabled_kinds

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(MemoryWriteInput, arguments)
        explicit_intent = has_explicit_memory_intent(self.user_prompt)
        if not explicit_intent and parsed.kind is not MemoryKind.EXPERIENCE:
            return ToolResult(
                "autonomous memory writes are limited to reusable engineering experiences",
                is_error=True,
                data={"error": "explicit_memory_intent_required"},
            )
        classified_kind = classify_memory_intent(self.user_prompt)
        kind = (
            MemoryKind.EXPERIENCE
            if classified_kind is MemoryKind.EXPERIENCE
            else parsed.kind or classified_kind
        )
        if kind is None:
            return ToolResult(
                "memory kind could not be determined",
                is_error=True,
                data={"error": "memory_kind_required"},
            )
        if kind not in self.enabled_kinds:
            return ToolResult(
                f"memory kind is disabled: {kind.value}",
                is_error=True,
                data={"error": "memory_kind_disabled", "kind": kind.value},
            )
        project_fact = kind is not MemoryKind.USER_PROFILE and (
            kind is not MemoryKind.REFERENCE or parsed.scope is MemoryScope.PROJECT
        )
        scope = parsed.scope or (MemoryScope.PROJECT if project_fact else MemoryScope.USER)
        if kind is MemoryKind.USER_PROFILE and scope is not MemoryScope.USER:
            return ToolResult(
                "user profile memories must use user scope",
                is_error=True,
                data={"error": "invalid_memory_scope"},
            )
        content = parsed.content.strip()
        try:
            validate_memory_text(content, self.user_prompt)
        except SensitiveMemoryError as exc:
            return ToolResult(str(exc), is_error=True, data={"error": "sensitive_memory_rejected"})
        # Spec 1: autonomous experience writes must pass model validation gate.
        # Spec 4a/b: project knowledge activation and title/summary quality use model-driven paths.
        evidence = () if kind is MemoryKind.SOP else (self.user_prompt,)
        refined: RefinedMemory
        if kind is MemoryKind.EXPERIENCE and not explicit_intent:
            assessment = await assess_experience(
                self.model,
                text=content,
                evidence=evidence,
            )
            if not assessment.should_store:
                return ToolResult(
                    f"experience not stored: {assessment.reason}",
                    is_error=True,
                    data={"error": "experience_assessment_rejected", "reason": assessment.reason},
                )
            if assessment.memory is None:
                return ToolResult(
                    "experience assessment returned no structured memory",
                    is_error=True,
                    data={"error": "experience_assessment_malformed"},
                )
            refined = assessment.memory
        else:
            refined = await refine_memory(
                self.model,
                text=content,
                kind=kind,
                evidence=evidence,
            )
        normalized = " ".join(refined.body.casefold().split())
        existing = next(
            (
                record
                for record in await self.service.list()
                if record.kind is kind
                and record.scope is scope
                and " ".join(record.body.casefold().split()) == normalized
            ),
            None,
        )
        if existing is not None:
            data = {
                "memory_id": existing.memory_id,
                "status": existing.status.value,
                "kind": existing.kind.value,
                "scope": existing.scope.value,
                "result": "already_exists",
            }
            await self._observe("already_exists", data)
            return ToolResult(json.dumps(data, ensure_ascii=False), data=data)
        activation = None
        if kind is MemoryKind.PROJECT_KNOWLEDGE:
            core = explicitly_always_project_fact(
                self.user_prompt
            ) or await assess_core_project_fact(self.model, text=content)
            activation = MemoryActivation.ALWAYS if core else MemoryActivation.MANUAL
        priority = 60 if activation is MemoryActivation.ALWAYS else None
        candidate = await self.service.create_candidate(
            kind=kind,
            scope=scope,
            title=refined.title,
            summary=refined.summary,
            body=refined.body,
            source=self.source,
            tags=refined.tags,
            evidence=evidence,
            confidence=0.8,
            activation=activation,
            priority=priority,
        )
        if kind is MemoryKind.SOP:
            record = candidate
            action = "candidate_created"
        else:
            record = await self.service.transition(candidate.memory_id, MemoryStatus.ACTIVE)
            action = "activated"
        data = {
            "memory_id": record.memory_id,
            "status": record.status.value,
            "kind": record.kind.value,
            "scope": record.scope.value,
            "result": "stored",
        }
        await self._observe(action, data)
        return ToolResult(json.dumps(data, ensure_ascii=False), data=data)


class MemoryUpdateTool(_MemoryTool):
    name = "memory_update"
    description = (
        "Update one existing visible long-term memory in place by exact ID or unique ID prefix. "
        "Use memory_search or memory_get first, then provide the complete revised content. "
        "Do not edit memory files or databases and do not create a replacement memory."
    )
    input_model = MemoryUpdateInput
    effects = frozenset({ToolEffect.OUTSIDE_WORKSPACE})

    def __init__(
        self,
        service: MemoryService,
        observer: MemoryToolObserver,
        *,
        max_chars: int,
        user_prompt: str,
        model: ModelTarget,
        enabled_kinds: frozenset[MemoryKind] | None = None,
    ) -> None:
        super().__init__(service, observer, max_chars=max_chars)
        self.user_prompt = user_prompt
        self.model = model
        self.enabled_kinds = frozenset(MemoryKind) if enabled_kinds is None else enabled_kinds

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(MemoryUpdateInput, arguments)
        try:
            current = await self.service.resolve_prefix(parsed.memory_id)
        except ValueError:
            return ToolResult(
                "memory ID does not exist or prefix is not unique",
                is_error=True,
                data={"error": "memory_not_found_or_ambiguous"},
            )
        if current.kind not in self.enabled_kinds:
            return ToolResult(
                f"memory kind is disabled: {current.kind.value}",
                is_error=True,
                data={"error": "memory_kind_disabled", "kind": current.kind.value},
            )
        content = parsed.content.strip()
        try:
            validate_memory_text(content, self.user_prompt)
        except SensitiveMemoryError as exc:
            return ToolResult(str(exc), is_error=True, data={"error": "sensitive_memory_rejected"})
        refined = await refine_memory(
            self.model,
            text=content,
            kind=current.kind,
            evidence=(self.user_prompt,),
        )
        record = await self.service.update(
            current.memory_id,
            title=refined.title,
            summary=refined.summary,
            body=refined.body,
        )
        data = {
            "memory_id": record.memory_id,
            "status": record.status.value,
            "kind": record.kind.value,
            "scope": record.scope.value,
            "version": record.version,
            "result": "updated",
        }
        await self._observe("updated", data)
        return ToolResult(json.dumps(data, ensure_ascii=False), data=data)


class MemoryDeleteTool(_MemoryTool):
    name = "memory_delete"
    description = (
        "Permanently delete one visible long-term memory by exact ID or unique ID prefix, only "
        "when the full conversation context shows that the user wants it deleted or forgotten. "
        "Use memory_search or memory_get first. Never delete memory files or database rows with "
        "other tools."
    )
    input_model = MemoryDeleteInput
    effects = frozenset({ToolEffect.OUTSIDE_WORKSPACE})

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(MemoryDeleteInput, arguments)
        try:
            record = await self.service.resolve_prefix(parsed.memory_id)
        except ValueError:
            return ToolResult(
                "memory ID does not exist or prefix is not unique",
                is_error=True,
                data={"error": "memory_not_found_or_ambiguous"},
            )
        await self.service.delete(record.memory_id)
        data = {
            "memory_id": record.memory_id,
            "kind": record.kind.value,
            "scope": record.scope.value,
            "result": "deleted",
        }
        await self._observe("deleted", data)
        return ToolResult(json.dumps(data, ensure_ascii=False), data=data)


def register_memory_tools(
    registry: ToolRegistry,
    service: MemoryService,
    observer: MemoryToolObserver,
    *,
    max_chars: int,
    user_prompt: str,
    source: MemorySource,
    model: ModelTarget,
    enabled_kinds: frozenset[MemoryKind] | None = None,
) -> None:
    for tool in (
        MemorySearchTool(service, observer, max_chars=max_chars),
        MemoryListTool(service, observer, max_chars=max_chars),
        MemoryGetTool(service, observer, max_chars=max_chars),
        MemoryDeleteTool(service, observer, max_chars=max_chars),
        MemoryUpdateTool(
            service,
            observer,
            max_chars=max_chars,
            user_prompt=user_prompt,
            model=model,
            enabled_kinds=enabled_kinds,
        ),
        MemoryWriteTool(
            service,
            observer,
            max_chars=max_chars,
            user_prompt=user_prompt,
            source=source,
            model=model,
            enabled_kinds=enabled_kinds,
        ),
    ):
        registry.register(tool)
