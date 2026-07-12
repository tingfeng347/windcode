from __future__ import annotations

from dataclasses import dataclass

from windcode.domain.messages import SourcedContextMessage
from windcode.extensions.models import (
    ActivationState,
    CapabilityKind,
    CapabilityRecord,
    ExtensionSnapshot,
)
from windcode.extensions.skills.loader import SkillContent, SkillLoader
from windcode.extensions.skills.parser import SkillMetadata


@dataclass(frozen=True, slots=True)
class SkillSearchResult:
    capability_id: str
    name: str
    description: str
    source_id: str
    shadowed_by: str | None


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
