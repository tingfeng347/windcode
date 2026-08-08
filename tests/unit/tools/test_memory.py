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
    MemoryGetInput,
    MemoryGetTool,
    MemoryListInput,
    MemoryListTool,
    MemorySearchInput,
    MemorySearchTool,
    MemoryWriteInput,
    MemoryWriteTool,
)


def context(workspace: Path) -> ToolContext:
    return ToolContext(workspace, "run", lambda: False)


async def test_memory_tools_search_list_and_get_visible_records(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = MemoryService(tmp_path / "state", workspace)
    user = service.create_candidate(
        kind=MemoryKind.USER_PROFILE,
        scope=MemoryScope.USER,
        title="语言偏好",
        summary="用户偏好中文回答",
        body="始终使用中文回答。",
    )
    service.store.transition(user.memory_id, MemoryStatus.ACTIVE)
    project = service.create_candidate(
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        title="项目语言",
        summary="项目使用 Python",
        body="本项目使用 Python 3.12。",
    )
    service.store.transition(project.memory_id, MemoryStatus.ACTIVE)
    candidate = service.create_candidate(
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
    hidden = first.create_candidate(
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        title="私有项目事实",
        summary="只属于项目 A",
        body="项目 A 的内部约定。",
    )
    first.store.transition(hidden.memory_id, MemoryStatus.ACTIVE)
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
    assert len(service.store.list(project_id=service.project_id)) == 1
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
    record = service.store.get(str(result.data["memory_id"]))
    assert record.activation is MemoryActivation.SEARCH
    assert record.evidence


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
    assert service.store.get(str(result.data["memory_id"])).kind is MemoryKind.EXPERIENCE


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
