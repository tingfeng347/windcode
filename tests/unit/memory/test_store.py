from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from windcode.memory import (
    MemoryActivation,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
    MemoryStore,
    SensitiveMemoryError,
    project_identifier,
)


def record(
    *,
    scope: MemoryScope = MemoryScope.USER,
    project_id: str | None = None,
    kind: MemoryKind = MemoryKind.USER_PROFILE,
    evidence: tuple[str, ...] = (),
) -> MemoryRecord:
    return MemoryRecord.create(
        kind=kind,
        scope=scope,
        project_id=project_id,
        title="Prefer focused tests",
        summary="The user prefers focused tests before full suites",
        body="Run focused pytest targets before the complete test suite.",
        tags=("testing", "workflow"),
        evidence=evidence,
        confidence=0.9,
    )


def test_markdown_is_source_of_truth_and_index_rebuilds(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    saved = store.save(record())
    assert store.get(saved.memory_id) == saved
    store.index_path.unlink()

    rebuilt = MemoryStore(tmp_path)
    assert rebuilt.rebuild() == 1
    assert rebuilt.get(saved.memory_id) == saved


def test_candidate_does_not_recall_until_confirmed(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    saved = store.save(record())
    assert store.search("focused tests", project_id="project") == ()

    active = store.transition(saved.memory_id, MemoryStatus.ACTIVE)
    results = store.search("focused tests", project_id="project")
    assert results[0].record == active
    assert active.version == 2


def test_project_memory_is_isolated(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    saved = store.save(record(scope=MemoryScope.PROJECT, project_id="project-a"))
    store.transition(saved.memory_id, MemoryStatus.ACTIVE)
    assert store.search("focused tests", project_id="project-b") == ()
    assert store.search("focused tests", project_id="project-a")


def test_experience_can_activate_without_evidence(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    candidate = store.save(record(kind=MemoryKind.EXPERIENCE))
    active = store.transition(candidate.memory_id, MemoryStatus.ACTIVE)
    assert active.status is MemoryStatus.ACTIVE
    assert active.evidence == ()


def test_sensitive_data_is_rejected_without_writing(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    sensitive = MemoryRecord.create(
        kind=MemoryKind.REFERENCE,
        scope=MemoryScope.USER,
        title="Credential",
        summary="Never persist credentials",
        body="api_key = sk-1234567890abcdefghijklmnop",
    )
    with pytest.raises(SensitiveMemoryError):
        store.save(sensitive)
    assert tuple(store.records_dir.rglob("*.md")) == ()


def test_delete_removes_markdown_and_index(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    saved = store.save(record())
    store.delete(saved.memory_id)
    with pytest.raises(KeyError):
        store.get(saved.memory_id)
    assert tuple(store.records_dir.rglob("*.md")) == ()


def test_project_identifier_is_stable_and_path_specific(tmp_path: Path) -> None:
    assert project_identifier(tmp_path) == project_identifier(tmp_path / ".")
    assert project_identifier(tmp_path) != project_identifier(tmp_path / "other")


def test_cjk_lexical_fallback_recalls_related_fact(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    saved = MemoryRecord.create(
        kind=MemoryKind.USER_PROFILE,
        scope=MemoryScope.USER,
        title="我喜欢 Python",
        summary="用户喜欢 Python",
        body="我喜欢使用 Python 编写终端工具",
    )
    store.save(saved)
    store.transition(saved.memory_id, MemoryStatus.ACTIVE)
    results = store.search("我喜欢什么？", project_id="project")  # noqa: RUF001
    assert results[0].record.memory_id == saved.memory_id


def test_missing_activation_fields_use_legacy_defaults() -> None:
    raw = record().to_dict()
    raw.pop("activation")
    raw.pop("priority")
    restored = MemoryRecord.from_dict(raw)
    assert restored.activation is MemoryActivation.ALWAYS
    assert restored.priority == 80

    raw["kind"] = MemoryKind.REFERENCE.value
    restored_reference = MemoryRecord.from_dict(raw)
    assert restored_reference.activation is MemoryActivation.SEARCH
    assert restored_reference.priority == 40


def test_sop_defaults_and_priority_validation(tmp_path: Path) -> None:
    sop = MemoryRecord.create(
        kind=MemoryKind.SOP,
        scope=MemoryScope.PROJECT,
        project_id="project",
        title="Release workflow",
        summary="Verified release steps",
        body="Build, test, then publish.",
    )
    assert sop.activation is MemoryActivation.SEARCH
    assert sop.priority == 70
    with pytest.raises(ValueError, match="priority"):
        MemoryStore(tmp_path).save(replace(record(), priority=101))
