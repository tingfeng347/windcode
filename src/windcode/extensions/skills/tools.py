from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.messages import SourcedContextMessage
from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.extensions.models import (
    ActivationState,
    CapabilityKind,
    CapabilityRecord,
    ExtensionSnapshot,
)
from windcode.extensions.skills.loader import SkillContent, SkillLoader
from windcode.extensions.skills.parser import SkillMetadata
from windcode.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class SkillSearchResult:
    capability_id: str
    name: str
    description: str
    source_id: str
    shadowed_by: str | None


@dataclass(frozen=True, slots=True)
class SkillActivationResult:
    name: str
    source_id: str
    digest: str
    loaded: bool


class SkillCatalog:
    def __init__(self, snapshot: ExtensionSnapshot, loader: SkillLoader) -> None:
        self.snapshot = snapshot
        self.loader = loader

    def search(self, query: str = "") -> tuple[SkillSearchResult, ...]:
        needle = query.casefold().strip()
        results: list[SkillSearchResult] = []
        for record in self.snapshot.capabilities:
            if record.kind is not CapabilityKind.SKILL:
                continue
            if (
                not record.enabled
                or not record.trusted
                or record.shadowed_by is not None
                or record.activation is ActivationState.FAILED
            ):
                continue
            metadata = self.snapshot.definitions.get(record.capability_id)
            if not isinstance(metadata, SkillMetadata):
                continue
            if (
                needle
                and needle not in metadata.name.casefold()
                and needle not in metadata.description.casefold()
            ):
                continue
            results.append(
                SkillSearchResult(
                    record.capability_id,
                    metadata.name,
                    metadata.description,
                    record.source.source_id,
                    record.shadowed_by,
                )
            )
        return tuple(results)

    def load(self, selector: str) -> tuple[SkillContent, SourcedContextMessage]:
        normalized = selector.removeprefix("$").casefold()
        matches: list[tuple[CapabilityRecord, SkillMetadata]] = []
        for record in self.snapshot.capabilities:
            metadata = self.snapshot.definitions.get(record.capability_id)
            if (
                record.kind is CapabilityKind.SKILL
                and record.enabled
                and record.trusted
                and record.shadowed_by is None
                and isinstance(metadata, SkillMetadata)
                and (record.capability_id == selector or metadata.name == normalized)
            ):
                matches.append((record, metadata))
        if len(matches) != 1:
            raise ValueError(f"Skill selector is missing or ambiguous: {selector}")
        content = self.loader.load(*matches[0])
        return content, SourcedContextMessage(content.source_id, content.content)


class SkillRuntime:
    """Per-run Skill catalog, activation state, and pending sourced context."""

    def __init__(self, catalog: SkillCatalog) -> None:
        self.catalog = catalog
        self._loaded: set[tuple[str, str]] = set()
        self._pending_context: list[SourcedContextMessage] = []

    def search(self, query: str = "") -> tuple[SkillSearchResult, ...]:
        return self.catalog.search(query)

    def activate(self, selector: str) -> SkillActivationResult:
        content, message = self.catalog.load(selector)
        key = (content.source_id, content.digest)
        loaded = key not in self._loaded
        if loaded:
            self._loaded.add(key)
            self._pending_context.append(message)
        return SkillActivationResult(content.name, content.source_id, content.digest, loaded)

    def drain_context(self) -> tuple[SourcedContextMessage, ...]:
        messages = tuple(self._pending_context)
        self._pending_context.clear()
        return messages


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchSkillsInput(_StrictInput):
    query: str = Field(default="", max_length=2_000)


class LoadSkillInput(_StrictInput):
    name: str = Field(min_length=1, max_length=256)


SkillActivator = Callable[[str], Awaitable[SkillActivationResult]]


class SearchSkillsTool:
    name = "search_skills"
    description = (
        "Search enabled and trusted Agent Skills by name or description. Results contain compact "
        "metadata only. Use load_skill with an exact result name to load its instructions."
    )
    input_model = SearchSkillsInput
    effects = frozenset[ToolEffect]()

    def __init__(self, runtime: SkillRuntime) -> None:
        self.runtime = runtime

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(SearchSkillsInput, arguments)
        results = self.runtime.search(parsed.query)
        data = {
            "skills": [
                {
                    "capability_id": item.capability_id,
                    "name": item.name,
                    "description": item.description,
                    "source_id": item.source_id,
                }
                for item in results
            ]
        }
        return ToolResult(json.dumps(data, ensure_ascii=False, sort_keys=True), data=data)


class LoadSkillTool:
    name = "load_skill"
    description = (
        "Load one enabled and trusted Agent Skill into the next model step. Pass an exact name "
        "returned by search_skills. Repeated loads in one run do not duplicate instructions."
    )
    input_model = LoadSkillInput
    effects = frozenset({ToolEffect.READ})

    def __init__(self, activate: SkillActivator) -> None:
        self.activate = activate

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(LoadSkillInput, arguments)
        try:
            result = await self.activate(parsed.name)
        except ValueError as exc:
            data = {
                "error": "skill_unavailable",
                "message": str(exc),
                "name": parsed.name,
            }
            return ToolResult(
                json.dumps(data, ensure_ascii=False, sort_keys=True),
                is_error=True,
                data=data,
            )
        data = {
            "name": result.name,
            "source_id": result.source_id,
            "status": "loaded" if result.loaded else "already_loaded",
        }
        return ToolResult(json.dumps(data, ensure_ascii=False, sort_keys=True), data=data)


def register_skill_tools(
    registry: ToolRegistry,
    runtime: SkillRuntime,
    activate: SkillActivator,
    *,
    replace: bool = False,
) -> None:
    registry.register(SearchSkillsTool(runtime), replace=replace)
    registry.register(LoadSkillTool(activate), replace=replace)
