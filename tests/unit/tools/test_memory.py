from __future__ import annotations

from pathlib import Path

from windcode.domain.tools import ToolContext, ToolEffect
from windcode.memory import (
    MemoryActivation,
    MemoryKind,
    MemoryScope,
    MemoryService,
    MemorySource,
    MemoryStatus,
)
from windcode.tools.memory import (
    MemoryDeleteInput,
    MemoryDeleteTool,
    MemoryGetInput,
    MemoryGetTool,
    MemoryListInput,
    MemoryListTool,
    MemorySearchInput,
    MemorySearchTool,
    MemoryUpdateInput,
    MemoryUpdateTool,
    MemoryWriteInput,
    MemoryWriteTool,
)


def context(workspace: Path) -> ToolContext:
    return ToolContext(workspace, "run", lambda: False)


async def test_memory_tools_search_list_and_get_visible_records(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = MemoryService(tmp_path / "state", workspace)
    user = await service.create_candidate(
        kind=MemoryKind.USER_PROFILE,
        scope=MemoryScope.USER,
        title="语言偏好",
        summary="用户偏好中文回答",
        body="始终使用中文回答。",
    )
    await service.store.transition(user.memory_id, MemoryStatus.ACTIVE)
    project = await service.create_candidate(
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        title="项目语言",
        summary="项目使用 Python",
        body="本项目使用 Python 3.12。",
    )
    await service.store.transition(project.memory_id, MemoryStatus.ACTIVE)
    candidate = await service.create_candidate(
        kind=MemoryKind.EXPERIENCE,
        scope=MemoryScope.PROJECT,
        title="提交经验",
        summary="生成 commit 前检查 diff",
        body="生成 commit 前检查 diff。",
    )
    observed: list[tuple[str, dict[str, object]]] = []

    async def observe(action: str, details: dict[str, object]) -> None:
        observed.append((action, details))

    search = MemorySearchTool(service, observe, max_chars=4_000)
    result = await search.execute(
        context(workspace),
        MemorySearchInput(query="Python", kind=MemoryKind.PROJECT_KNOWLEDGE),
    )
    assert result.data["count"] == 1
    assert result.data["memories"][0]["memory_id"] == project.memory_id

    result = await search.execute(
        context(workspace),
        MemorySearchInput(query="commit", kind=MemoryKind.EXPERIENCE),
    )
    assert result.data["count"] == 1
    assert result.data["memories"][0]["memory_id"] == candidate.memory_id
    assert result.data["memories"][0]["status"] == MemoryStatus.CANDIDATE.value

    listing = MemoryListTool(service, observe, max_chars=4_000)
    result = await listing.execute(context(workspace), MemoryListInput())
    assert result.data["count"] == 3
    assert {item["status"] for item in result.data["memories"]} == {"active", "candidate"}

    result = await listing.execute(context(workspace), MemoryListInput(status=MemoryStatus.ACTIVE))
    assert result.data["count"] == 2
    assert candidate.memory_id not in {item["memory_id"] for item in result.data["memories"]}

    get = MemoryGetTool(service, observe, max_chars=4_000)
    result = await get.execute(context(workspace), MemoryGetInput(memory_id=user.memory_id[:10]))
    assert result.data["memory"]["body"] == "始终使用中文回答。"
    assert [action for action, _ in observed] == [
        "searched",
        "searched",
        "listed",
        "listed",
        "retrieved",
    ]


async def test_memory_get_cannot_read_another_projects_record(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first = MemoryService(state, tmp_path / "first")
    hidden = await first.create_candidate(
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        title="私有项目事实",
        summary="只属于项目 A",
        body="项目 A 的内部约定。",
    )
    await first.store.transition(hidden.memory_id, MemoryStatus.ACTIVE)
    second = MemoryService(state, tmp_path / "second")

    async def observe(action: str, details: dict[str, object]) -> None:
        del action, details

    tool = MemoryGetTool(second, observe, max_chars=4_000)
    result = await tool.execute(
        context(second.workspace),
        MemoryGetInput(memory_id=hidden.memory_id),
    )
    assert result.is_error
    assert result.data["error"] == "memory_not_found_or_ambiguous"


async def test_memory_write_stores_explicit_user_fact_and_deduplicates(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = MemoryService(tmp_path / "state", workspace)
    observed: list[tuple[str, dict[str, object]]] = []

    async def observe(action: str, details: dict[str, object]) -> None:
        observed.append((action, details))

    tool = MemoryWriteTool(
        service,
        observe,
        max_chars=4_000,
        user_prompt="给我记住, 我偏好简洁回答",
        source=MemorySource("session", "run"),
    )
    assert tool.effects == frozenset({ToolEffect.OUTSIDE_WORKSPACE})
    arguments = MemoryWriteInput(
        content="用户偏好简洁回答。",
        kind=MemoryKind.USER_PROFILE,
    )
    first = await tool.execute(context(workspace), arguments)
    second = await tool.execute(context(workspace), arguments)

    assert first.data["result"] == "stored"
    assert first.data["status"] == MemoryStatus.ACTIVE.value
    assert second.data == {**first.data, "result": "already_exists"}
    assert len(await service.store.list(project_id=service.project_id)) == 1
    assert [action for action, _ in observed] == ["activated", "already_exists"]


async def test_memory_write_requires_explicit_intent_and_rejects_sensitive_data(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = MemoryService(tmp_path / "state", workspace)

    async def observe(action: str, details: dict[str, object]) -> None:
        del action, details

    implicit = MemoryWriteTool(
        service,
        observe,
        max_chars=4_000,
        user_prompt="我今天在调试登录功能",
        source=MemorySource("session", "run"),
    )
    result = await implicit.execute(
        context(workspace),
        MemoryWriteInput(content="用户在调试登录功能", kind=MemoryKind.USER_PROFILE),
    )
    assert result.is_error
    assert result.data["error"] == "explicit_memory_intent_required"

    sensitive = MemoryWriteTool(
        service,
        observe,
        max_chars=4_000,
        user_prompt="记住这个 API key: abc",
        source=MemorySource("session", "run"),
    )
    result = await sensitive.execute(
        context(workspace),
        MemoryWriteInput(content="API key: abc", kind=MemoryKind.REFERENCE),
    )
    assert result.is_error
    assert result.data["error"] == "sensitive_memory_rejected"


async def test_memory_write_activates_experience_without_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = MemoryService(tmp_path / "state", workspace)

    async def observe(action: str, details: dict[str, object]) -> None:
        del action, details

    tool = MemoryWriteTool(
        service,
        observe,
        max_chars=4_000,
        user_prompt="记住一条经验: 修改后先运行 focused tests",
        source=MemorySource("session", "run"),
    )
    result = await tool.execute(
        context(workspace),
        MemoryWriteInput(
            content="修改后先运行 focused tests。",
            kind=MemoryKind.EXPERIENCE,
        ),
    )
    assert result.data["status"] == MemoryStatus.ACTIVE.value
    record = await service.store.get(str(result.data["memory_id"]))
    assert record.activation is MemoryActivation.SEARCH
    assert record.evidence


async def test_memory_write_allows_model_selected_experience_without_explicit_intent(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = MemoryService(tmp_path / "state", workspace)

    async def observe(action: str, details: dict[str, object]) -> None:
        del action, details

    tool = MemoryWriteTool(
        service,
        observe,
        max_chars=4_000,
        user_prompt="修复解析器后, focused tests 证明边界检查应放在规范化之后",
        source=MemorySource("session", "run"),
    )
    result = await tool.execute(
        context(workspace),
        MemoryWriteInput(
            content="解析器的边界检查应放在输入规范化之后, 并用 focused tests 验证。",
            kind=MemoryKind.EXPERIENCE,
        ),
    )

    assert not result.is_error
    assert result.data["result"] == "stored"
    assert result.data["kind"] == MemoryKind.EXPERIENCE.value


async def test_memory_update_revises_visible_experience_in_place(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = MemoryService(tmp_path / "state", workspace)
    original = await service.create_candidate(
        kind=MemoryKind.EXPERIENCE,
        scope=MemoryScope.PROJECT,
        title="解析器经验",
        summary="先校验再规范化",
        body="解析器应先校验再规范化。",
    )
    original = await service.store.transition(original.memory_id, MemoryStatus.ACTIVE)
    observed: list[tuple[str, dict[str, object]]] = []

    async def observe(action: str, details: dict[str, object]) -> None:
        observed.append((action, details))

    tool = MemoryUpdateTool(
        service,
        observe,
        max_chars=4_000,
        user_prompt="修复解析器并验证了正确顺序",
    )
    result = await tool.execute(
        context(workspace),
        MemoryUpdateInput(
            memory_id=original.memory_id[:10],
            content="解析器应先规范化输入, 再执行边界检查。",
        ),
    )

    updated = await service.store.get(original.memory_id)
    assert not result.is_error
    assert result.data["result"] == "updated"
    assert result.data["memory_id"] == original.memory_id
    assert updated.body == "解析器应先规范化输入, 再执行边界检查。"
    assert updated.version == original.version + 1
    assert len(await service.store.list(project_id=service.project_id)) == 1
    assert [action for action, _ in observed] == ["updated"]


async def test_memory_update_rejects_hidden_non_experience_and_sensitive_content(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    first = MemoryService(state, tmp_path / "first")
    hidden = await first.create_candidate(
        kind=MemoryKind.EXPERIENCE,
        scope=MemoryScope.PROJECT,
        title="项目经验",
        summary="只属于项目 A",
        body="项目 A 的经验。",
    )
    await first.store.transition(hidden.memory_id, MemoryStatus.ACTIVE)
    second = MemoryService(state, tmp_path / "second")
    profile = await second.create_candidate(
        kind=MemoryKind.USER_PROFILE,
        scope=MemoryScope.USER,
        title="回答偏好",
        summary="偏好简洁回答",
        body="用户偏好简洁回答。",
    )
    await second.store.transition(profile.memory_id, MemoryStatus.ACTIVE)
    visible = await second.create_candidate(
        kind=MemoryKind.EXPERIENCE,
        scope=MemoryScope.PROJECT,
        title="可见经验",
        summary="当前项目经验",
        body="当前项目的经验。",
    )
    await second.store.transition(visible.memory_id, MemoryStatus.ACTIVE)

    async def observe(action: str, details: dict[str, object]) -> None:
        del action, details

    tool = MemoryUpdateTool(
        second,
        observe,
        max_chars=4_000,
        user_prompt="整理已有经验",
    )
    assert tool.effects == frozenset({ToolEffect.OUTSIDE_WORKSPACE})
    hidden_result = await tool.execute(
        context(second.workspace),
        MemoryUpdateInput(memory_id=hidden.memory_id, content="覆盖其他项目经验。"),
    )
    profile_result = await tool.execute(
        context(second.workspace),
        MemoryUpdateInput(memory_id=profile.memory_id, content="改变用户偏好。"),
    )
    sensitive_result = await tool.execute(
        context(second.workspace),
        MemoryUpdateInput(memory_id=visible.memory_id, content="API key: abc"),
    )

    assert hidden_result.data["error"] == "memory_not_found_or_ambiguous"
    assert profile_result.data["error"] == "memory_kind_not_updatable"
    assert sensitive_result.data["error"] == "sensitive_memory_rejected"


async def test_memory_delete_removes_visible_memory_without_keyword_gate(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = MemoryService(state, workspace)
    memory = await service.create_candidate(
        kind=MemoryKind.EXPERIENCE,
        scope=MemoryScope.PROJECT,
        title="旧经验",
        summary="已经失效的经验",
        body="旧的处理方式。",
    )
    await service.store.transition(memory.memory_id, MemoryStatus.ACTIVE)
    observed: list[tuple[str, dict[str, object]]] = []

    async def observe(action: str, details: dict[str, object]) -> None:
        observed.append((action, details))

    tool = MemoryDeleteTool(
        service,
        observe,
        max_chars=4_000,
    )
    result = await tool.execute(
        context(workspace),
        MemoryDeleteInput(memory_id=memory.memory_id[:10]),
    )

    assert not result.is_error
    assert result.data["result"] == "deleted"
    assert result.data["memory_id"] == memory.memory_id
    assert await service.store.list(project_id=service.project_id) == ()
    assert [action for action, _ in observed] == ["deleted"]


async def test_memory_delete_cannot_delete_another_projects_record(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    first = MemoryService(state, tmp_path / "first")
    hidden = await first.create_candidate(
        kind=MemoryKind.EXPERIENCE,
        scope=MemoryScope.PROJECT,
        title="项目经验",
        summary="只属于项目 A",
        body="项目 A 的经验。",
    )
    await first.store.transition(hidden.memory_id, MemoryStatus.ACTIVE)
    second = MemoryService(state, tmp_path / "second")

    async def observe(action: str, details: dict[str, object]) -> None:
        del action, details

    tool = MemoryDeleteTool(
        second,
        observe,
        max_chars=4_000,
    )
    hidden_result = await tool.execute(
        context(second.workspace), MemoryDeleteInput(memory_id=hidden.memory_id)
    )

    assert hidden_result.data["error"] == "memory_not_found_or_ambiguous"
    assert (await first.store.get(hidden.memory_id)).memory_id == hidden.memory_id


async def test_memory_write_user_experience_intent_overrides_model_sop_kind(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = MemoryService(tmp_path / "state", workspace)

    async def observe(action: str, details: dict[str, object]) -> None:
        del action, details

    tool = MemoryWriteTool(
        service,
        observe,
        max_chars=4_000,
        user_prompt="把 commit 工作流程的经验记下来",
        source=MemorySource("session", "run"),
    )
    result = await tool.execute(
        context(workspace),
        MemoryWriteInput(
            content="生成 commit 前先检查 status 和 diff。",
            kind=MemoryKind.SOP,
        ),
    )
    assert result.data["kind"] == MemoryKind.EXPERIENCE.value
    assert (await service.store.get(str(result.data["memory_id"]))).kind is MemoryKind.EXPERIENCE


async def test_memory_write_rejects_disabled_kind(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = MemoryService(tmp_path / "state", workspace)

    async def observe(action: str, details: dict[str, object]) -> None:
        del action, details

    tool = MemoryWriteTool(
        service,
        observe,
        max_chars=4_000,
        user_prompt="记住我的回答偏好",
        source=MemorySource("session", "run"),
        enabled_kinds=frozenset(),
    )
    result = await tool.execute(
        context(workspace),
        MemoryWriteInput(content="用户偏好简洁回答", kind=MemoryKind.USER_PROFILE),
    )
    assert result.is_error
    assert result.data["error"] == "memory_kind_disabled"
