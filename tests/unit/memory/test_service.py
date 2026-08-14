from __future__ import annotations

from pathlib import Path

import pytest

from windcode.memory import (
    MemoryActivation,
    MemoryKind,
    MemoryScope,
    MemoryService,
    MemoryStatus,
)


async def test_recall_is_bounded_and_marks_historical_context(tmp_path: Path) -> None:
    service = MemoryService(tmp_path / "state", tmp_path / "workspace")
    candidate = await service.create_candidate(
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        title="Architecture boundary",
        summary="Domain code is independent",
        body="Keep domain code independent from Textual widgets.",
    )
    await service.store.transition(candidate.memory_id, MemoryStatus.ACTIVE)
    context = await service.recall("Architecture boundary", max_chars=2_000)
    assert "历史观察" in context
    assert "Keep domain code independent" in context
    assert len(context) <= 2_000


async def test_user_memories_cross_projects_but_project_memories_do_not(tmp_path: Path) -> None:
    first = MemoryService(tmp_path / "state", tmp_path / "one")
    user = await first.create_candidate(
        kind=MemoryKind.USER_PROFILE,
        scope=MemoryScope.USER,
        title="Concise replies",
        summary="User prefers concise replies",
        body="Keep final answers concise.",
    )
    project = await first.create_candidate(
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        title="Windcode layers",
        summary="Windcode uses layered architecture",
        body="Keep orchestration in runtime.",
    )
    await first.store.transition(user.memory_id, MemoryStatus.ACTIVE)
    await first.store.transition(project.memory_id, MemoryStatus.ACTIVE)

    second = MemoryService(tmp_path / "state", tmp_path / "two")
    assert "concise" in (await second.recall("Concise replies")).casefold()
    unrelated_context = await second.recall("Windcode layers")
    assert "Concise replies" in unrelated_context
    assert "Windcode layers" not in unrelated_context


async def test_project_memory_mutations_are_isolated_by_project(tmp_path: Path) -> None:
    first = MemoryService(tmp_path / "state", tmp_path / "one")
    project = await first.create_candidate(
        kind=MemoryKind.EXPERIENCE,
        scope=MemoryScope.PROJECT,
        title="Private project experience",
        summary="Only project one may manage this record",
        body="Keep this experience inside project one.",
        evidence=("verified",),
    )
    await first.store.transition(project.memory_id, MemoryStatus.ACTIVE)
    second = MemoryService(tmp_path / "state", tmp_path / "two")

    with pytest.raises(ValueError, match="does not exist in this project"):
        await second.transition(project.memory_id, MemoryStatus.ARCHIVED)
    with pytest.raises(ValueError, match="does not exist in this project"):
        await second.update(project.memory_id, body="changed from project two")
    with pytest.raises(ValueError, match="does not exist in this project"):
        await second.delete(project.memory_id)
    with pytest.raises(ValueError, match="does not exist in this project"):
        await second.draft_skill(project.memory_id)

    unchanged = await first.get(project.memory_id)
    assert unchanged.status is MemoryStatus.ACTIVE
    assert unchanged.body == "Keep this experience inside project one."


async def test_layered_context_filters_activation_and_status(tmp_path: Path) -> None:
    service = MemoryService(tmp_path / "state", tmp_path / "workspace")
    for title, activation in (
        ("Always constraint", MemoryActivation.ALWAYS),
        ("Search workflow", MemoryActivation.SEARCH),
        ("Manual note", MemoryActivation.MANUAL),
    ):
        record = await service.create_candidate(
            kind=MemoryKind.PROJECT_KNOWLEDGE,
            scope=MemoryScope.PROJECT,
            title=title,
            summary=title,
            body=f"{title} body",
            activation=activation,
        )
        await service.store.transition(record.memory_id, MemoryStatus.ACTIVE)
    candidate = await service.create_candidate(
        kind=MemoryKind.SOP,
        scope=MemoryScope.PROJECT,
        title="Candidate workflow",
        summary="Candidate workflow",
        body="Candidate workflow body",
    )

    baseline = await service.baseline_context()
    dynamic = await service.search_context("workflow")
    combined = await service.build_context("workflow")
    assert "Always constraint" in baseline
    assert "Search workflow" not in baseline
    assert "Search workflow" in dynamic
    assert "Manual note" not in combined
    assert candidate.title not in combined


async def test_service_migrates_non_sop_candidates_and_reference_activation(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    workspace = tmp_path / "workspace"
    service = MemoryService(state_root, workspace)
    experience = await service.create_candidate(
        kind=MemoryKind.EXPERIENCE,
        scope=MemoryScope.PROJECT,
        title="Commit experience",
        summary="Inspect diff before commit",
        body="Inspect diff before commit.",
    )
    reference = await service.create_candidate(
        kind=MemoryKind.REFERENCE,
        scope=MemoryScope.USER,
        title="Commit reference",
        summary="Commit message reference",
        body="Use a scoped commit subject.",
        activation=MemoryActivation.MANUAL,
    )
    sop = await service.create_candidate(
        kind=MemoryKind.SOP,
        scope=MemoryScope.PROJECT,
        title="Commit SOP",
        summary="Commit steps",
        body="Run status, inspect diff, then commit.",
    )

    migrated = MemoryService(state_root, workspace)
    await migrated.migrate()

    assert (await migrated.store.get(experience.memory_id)).status is MemoryStatus.ACTIVE
    migrated_reference = await migrated.store.get(reference.memory_id)
    assert migrated_reference.status is MemoryStatus.ACTIVE
    assert migrated_reference.activation is MemoryActivation.SEARCH
    assert (await migrated.store.get(sop.memory_id)).status is MemoryStatus.CANDIDATE
    context = await migrated.search_context("commit")
    assert "Inspect diff before commit" in context
    assert "Use a scoped commit subject" in context
    assert "Run status, inspect diff, then commit" not in context


async def test_baseline_budget_prefers_priority(tmp_path: Path) -> None:
    service = MemoryService(tmp_path / "state", tmp_path / "workspace")
    for title, priority in (("Low", 10), ("High", 90)):
        record = await service.create_candidate(
            kind=MemoryKind.USER_PROFILE,
            scope=MemoryScope.USER,
            title=title,
            summary=title,
            body=f"{title} preference",
            priority=priority,
        )
        await service.store.transition(record.memory_id, MemoryStatus.ACTIVE)
    context = await service.baseline_context(max_records=1)
    assert "High" in context
    assert "Low" not in context


async def test_user_profile_candidate_flags_conflict_on_overlapping_tags(tmp_path: Path) -> None:
    service = MemoryService(tmp_path / "state", tmp_path / "workspace")
    existing = await service.create_candidate(
        kind=MemoryKind.USER_PROFILE,
        scope=MemoryScope.USER,
        title="用户姓名",
        summary="用户叫 tingfeng",
        body="用户姓名是 tingfeng。",
        tags=("姓名", "identity"),
    )
    await service.store.transition(existing.memory_id, MemoryStatus.ACTIVE)
    # 不同标题但 tags 交集 -> 冲突
    conflicting = await service.create_candidate(
        kind=MemoryKind.USER_PROFILE,
        scope=MemoryScope.USER,
        title="称呼",
        summary="用户叫小明",
        body="用户姓名是小明。",
        tags=("姓名",),
    )
    assert existing.memory_id in conflicting.conflicts_with
    # 无交集 tags -> 不冲突
    unrelated = await service.create_candidate(
        kind=MemoryKind.USER_PROFILE,
        scope=MemoryScope.USER,
        title="语言偏好",
        summary="偏好中文",
        body="使用中文回答。",
        tags=("语言",),
    )
    assert unrelated.conflicts_with == ()


async def test_non_user_profile_ignores_tags_intersection_for_conflict(tmp_path: Path) -> None:
    service = MemoryService(tmp_path / "state", tmp_path / "workspace")
    existing = await service.create_candidate(
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        title="项目语言",
        summary="项目用 Python",
        body="项目使用 Python。",
        tags=("python",),
    )
    await service.store.transition(existing.memory_id, MemoryStatus.ACTIVE)
    candidate = await service.create_candidate(
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        title="测试框架",
        summary="用 pytest",
        body="项目使用 pytest。",
        tags=("python",),
    )
    assert candidate.conflicts_with == ()
