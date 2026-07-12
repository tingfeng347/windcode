from __future__ import annotations

import hashlib
from dataclasses import dataclass

from windcode.extensions.models import CapabilityRecord
from windcode.extensions.paths import read_bounded
from windcode.extensions.skills.parser import SkillMetadata


@dataclass(frozen=True, slots=True)
class SkillContent:
    source_id: str
    name: str
    content: str
    digest: str


@dataclass(frozen=True, slots=True)
class SkillReference:
    source_id: str
    path: str
    content: bytes


class SkillLoader:
    def __init__(self, *, max_content_bytes: int) -> None:
        self.max_content_bytes = max_content_bytes
        self._content_cache: dict[tuple[str, str], SkillContent] = {}
        self._source_content: dict[str, SkillContent] = {}
        self._reference_cache: dict[tuple[str, str, str], SkillReference] = {}

    def load(self, record: CapabilityRecord, metadata: SkillMetadata) -> SkillContent:
        source_id = record.source.source_id
        existing = self._source_content.get(source_id)
        if existing is not None:
            return existing
        data = read_bounded(metadata.root, "SKILL.md", max_bytes=self.max_content_bytes)
        digest = record.source.digest or hashlib.sha256(data).hexdigest()
        key = (source_id, digest)
        cached = self._content_cache.get(key)
        if cached is not None:
            return cached
        try:
            text = data.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError(f"Skill content is not UTF-8: {source_id}") from exc
        content = SkillContent(source_id, metadata.name, text, digest)
        self._content_cache[key] = content
        self._source_content[source_id] = content
        return content

    def read_reference(
        self,
        record: CapabilityRecord,
        metadata: SkillMetadata,
        content: SkillContent,
        relative_path: str,
    ) -> SkillReference:
        key = (content.source_id, content.digest, relative_path)
        cached = self._reference_cache.get(key)
        if cached is not None:
            return cached
        data = read_bounded(metadata.root, relative_path, max_bytes=self.max_content_bytes)
        reference = SkillReference(record.source.source_id, relative_path, data)
        self._reference_cache[key] = reference
        return reference
