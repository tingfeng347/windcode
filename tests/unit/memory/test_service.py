from __future__ import annotations

from pathlib import Path

from windcode.memory import (
    MemoryActivation,
    MemoryKind,
    MemoryScope,
    MemoryService,
    MemoryStatus,
)


def test_recall_is_bounded_and_marks_historical_context(tmp_path: Path) -> None:
    service = MemoryService(tmp_path / "state", tmp_path / "workspace")
    candidate = service.create_candidate(
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        title="Architecture boundary",
        summary="Domain code is independent",
        body="Keep domain code independent from Textual widgets.",
    )
    service.store.transition(candidate.memory_id, MemoryStatus.ACTIVE)
    context = service.recall("Architecture boundary", max_chars=2_000)
    assert "历史观察" in context
    assert "Keep domain code independent" in context
    assert len(context) <= 2_000


def test_user_memories_cross_projects_but_project_memories_do_not(tmp_path: Path) -> None:
    first = MemoryService(tmp_path / "state", tmp_path / "one")
    user = first.create_candidate(
        kind=MemoryKind.USER_PROFILE,
        scope=MemoryScope.USER,
        title="Concise replies",
        summary="User prefers concise replies",
        body="Keep final answers concise.",
    )
    project = first.create_candidate(
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        title="Windcode layers",
        summary="Windcode uses layered architecture",
        body="Keep orchestration in runtime.",
    )
    first.store.transition(user.memory_id, MemoryStatus.ACTIVE)
    first.store.transition(project.memory_id, MemoryStatus.ACTIVE)

    second = MemoryService(tmp_path / "state", tmp_path / "two")
    assert "concise" in second.recall("Concise replies").casefold()
    unrelated_context = second.recall("Windcode layers")
    assert "Concise replies" in unrelated_context
    assert "Windcode layers" not in unrelated_context


def test_layered_context_filters_activation_and_status(tmp_path: Path) -> None:
    service = MemoryService(tmp_path / "state", tmp_path / "workspace")
    for title, activation in (
        ("Always constraint", MemoryActivation.ALWAYS),
        ("Search workflow", MemoryActivation.SEARCH),
        ("Manual note", MemoryActivation.MANUAL),
    ):
        record = service.create_candidate(
            kind=MemoryKind.PROJECT_KNOWLEDGE,
            scope=MemoryScope.PROJECT,
            title=title,
            summary=title,
            body=f"{title} body",
            activation=activation,
        )
        service.store.transition(record.memory_id, MemoryStatus.ACTIVE)
    candidate = service.create_candidate(
        kind=MemoryKind.SOP,
        scope=MemoryScope.PROJECT,
        title="Candidate workflow",
        summary="Candidate workflow",
        body="Candidate workflow body",
    )

    baseline = service.baseline_context()
    dynamic = service.search_context("workflow")
    combined = service.build_context("workflow")
    assert "Always constraint" in baseline
    assert "Search workflow" not in baseline
    assert "Search workflow" in dynamic
    assert "Manual note" not in combined
    assert candidate.title not in combined


def test_service_migrates_non_sop_candidates_and_reference_activation(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    workspace = tmp_path / "workspace"
    service = MemoryService(state_root, workspace)
    experience = service.create_candidate(
        kind=MemoryKind.EXPERIENCE,
        scope=MemoryScope.PROJECT,
        title="Commit experience",
        summary="Inspect diff before commit",
        body="Inspect diff before commit.",
    )
    reference = service.create_candidate(
        kind=MemoryKind.REFERENCE,
        scope=MemoryScope.USER,
        title="Commit reference",
        summary="Commit message reference",
        body="Use a scoped commit subject.",
        activation=MemoryActivation.MANUAL,
    )
    sop = service.create_candidate(
        kind=MemoryKind.SOP,
        scope=MemoryScope.PROJECT,
        title="Commit SOP",
        summary="Commit steps",
        body="Run status, inspect diff, then commit.",
    )

    migrated = MemoryService(state_root, workspace)

    assert migrated.store.get(experience.memory_id).status is MemoryStatus.ACTIVE
    migrated_reference = migrated.store.get(reference.memory_id)
    assert migrated_reference.status is MemoryStatus.ACTIVE
    assert migrated_reference.activation is MemoryActivation.SEARCH
    assert migrated.store.get(sop.memory_id).status is MemoryStatus.CANDIDATE
    context = migrated.search_context("commit")
    assert "Inspect diff before commit" in context
    assert "Use a scoped commit subject" in context
    assert "Run status, inspect diff, then commit" not in context


def test_baseline_budget_prefers_priority(tmp_path: Path) -> None:
    service = MemoryService(tmp_path / "state", tmp_path / "workspace")
    for title, priority in (("Low", 10), ("High", 90)):
        record = service.create_candidate(
            kind=MemoryKind.USER_PROFILE,
            scope=MemoryScope.USER,
            title=title,
            summary=title,
            body=f"{title} preference",
            priority=priority,
        )
        service.store.transition(record.memory_id, MemoryStatus.ACTIVE)
    context = service.baseline_context(max_records=1)
    assert "High" in context
    assert "Low" not in context
