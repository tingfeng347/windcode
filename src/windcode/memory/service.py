from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from windcode.memory.models import (
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemorySource,
    MemoryStatus,
)
from windcode.memory.store import MemoryStore, project_identifier


class MemoryService:
    def __init__(self, state_root: Path, workspace: Path) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.project_id = project_identifier(self.workspace)
        self.store = MemoryStore(state_root / "memory")

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
    ) -> MemoryRecord:
        candidate = MemoryRecord.create(
            kind=kind,
            scope=scope,
            title=title,
            summary=summary,
            body=body,
            project_id=self.project_id if scope is MemoryScope.PROJECT else None,
            source=source,
            tags=tags,
            evidence=evidence,
            confidence=confidence,
        )
        conflicts = tuple(
            record.memory_id
            for record in self.store.list(status=MemoryStatus.ACTIVE, project_id=self.project_id)
            if record.kind is kind
            and record.scope is scope
            and record.title.casefold() == title.casefold()
        )
        if conflicts:
            candidate = replace(candidate, conflicts_with=conflicts)
        return self.store.save(candidate)

    def recall(self, query: str, *, limit: int = 5, max_chars: int = 12_000) -> str:
        results = self.store.search(query, project_id=self.project_id, limit=limit)
        if not results:
            return ""
        sections: list[str] = [
            "# 按需召回的长期记忆",
            "记忆是历史观察; 项目事实需对照当前代码验证。",
        ]
        for result in results:
            record = result.record
            section = (
                f"\n## {record.title}\n"
                f"类型: {record.kind.value}; 范围: {record.scope.value}; "
                f"更新时间: {record.updated_at.isoformat()}\n{record.body}"
            )
            if sum(len(item) for item in sections) + len(section) > max_chars:
                break
            sections.append(section)
        return "\n".join(sections)

    def candidates(self) -> tuple[MemoryRecord, ...]:
        return self.store.list(status=MemoryStatus.CANDIDATE, project_id=self.project_id)

    def draft_skill(self, memory_id: str) -> str:
        record = self.store.get(memory_id)
        if record.kind is not MemoryKind.EXPERIENCE:
            raise ValueError("only experience memories can become skill drafts")
        if record.status is not MemoryStatus.ACTIVE or not record.evidence:
            raise ValueError("skill drafts require an active, verified experience")
        description = record.summary.replace("\n", " ").strip()
        return (
            f"---\nname: experience-{record.memory_id[:12]}\n"
            f"description: {description}\n---\n\n# {record.title}\n\n{record.body}\n\n"
            "## Verification evidence\n\n"
            + "\n".join(f"- {item}" for item in record.evidence)
            + "\n"
        )
