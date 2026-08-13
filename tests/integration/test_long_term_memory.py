from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from windcode import Windcode
from windcode.config import AppConfig, SandboxConfig
from windcode.domain.events import (
    ApprovalRequested,
    ApprovalResponse,
    MemoryEvent,
    RunRequest,
)
from windcode.domain.messages import TextBlock
from windcode.domain.models import (
    ModelCompleted,
    ModelEvent,
    ModelRequest,
    StopReason,
    TextDelta,
    ToolCallDelta,
)
from windcode.memory import (
    MemoryActivation,
    MemoryKind,
    MemoryScope,
    MemoryService,
    MemoryStatus,
)


class RecordingTransport:
    name = "recording"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield TextDelta("done")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class ExperienceTransport:
    name = "experience"

    def __init__(self, *, change_file: bool) -> None:
        self.change_file = change_file
        self.step = 0
        self.assessment_calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        block = request.messages[-1].content[0]
        if isinstance(block, TextBlock) and "should_store" in block.text:
            self.assessment_calls += 1
            yield TextDelta(
                '{"should_store":true,"reason":"形成可复用修复",'
                '"problem":"缺少模块文件","solution":"创建模块后执行 Ruff 验证",'
                '"applicability":"新增 Python 模块","title":"新增模块验证",'
                '"summary":"新增模块后运行 Ruff",'
                '"body":"创建模块并运行 ruff check 验证。","tags":["ruff"]}'
            )
            yield ModelCompleted(StopReason.STOP)
            return
        self.step += 1
        if self.change_file and self.step == 1:
            yield ToolCallDelta(
                "write",
                "write_file",
                '{"path":"lesson.py","content":"VALUE = 1\\n"}',
            )
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        if (self.change_file and self.step == 2) or (not self.change_file and self.step == 1):
            yield ToolCallDelta("check", "shell", '{"command":"ruff check ."}')
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        yield TextDelta("创建模块并通过 Ruff 验证。" if self.change_file else "Ruff 检查通过。")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class ExternalActionExperienceTransport:
    name = "external-action-experience"

    def __init__(self) -> None:
        self.step = 0
        self.assessment_calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        block = request.messages[-1].content[0]
        if isinstance(block, TextBlock) and "should_store" in block.text:
            self.assessment_calls += 1
            yield TextDelta(
                '{"should_store":true,"reason":"形成可复用安装方法",'
                '"problem":"插件尚未安装","solution":"通过 CLI 完成插件安装",'
                '"applicability":"安装同类插件","title":"CLI 安装插件",'
                '"summary":"使用 CLI 安装并确认插件可用",'
                '"body":"通过 CLI 安装插件并检查安装结果。","tags":["plugin"]}'
            )
            yield ModelCompleted(StopReason.STOP)
            return
        self.step += 1
        if self.step == 1:
            yield ToolCallDelta(
                "install",
                "shell",
                '{"command":"mkdir -p installed-plugin"}',
            )
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        yield TextDelta("插件已通过 CLI 安装并确认可用。")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class ActiveMemoryQueryTransport:
    name = "active-memory-query"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            assert {tool.name for tool in request.tools} >= {
                "memory_search",
                "memory_list",
                "memory_get",
            }
            assert "必须调用 memory_list" in request.system_prompt
            yield ToolCallDelta("memory", "memory_list", "{}")
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        yield TextDelta("长期记忆中保存了语言偏好。")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class MemoryWriteTransport:
    name = "memory-write"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            tool_names = {tool.name for tool in request.tools}
            assert "memory_write" in tool_names
            assert "memory_delete" in tool_names
            assert "只有工具返回 stored 或 already_exists 后" in request.system_prompt
            yield ToolCallDelta(
                "remember",
                "memory_write",
                '{"content":"用户偏好先运行 focused tests。","kind":"user_profile"}',
            )
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        yield TextDelta("已经写入长期记忆。")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class MemoryUpdateTransport:
    name = "memory-update"

    def __init__(self, memory_id: str) -> None:
        self.memory_id = memory_id
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            assert "memory_update" in {tool.name for tool in request.tools}
            assert "只能使用 memory_* 工具" in request.system_prompt
            yield ToolCallDelta(
                "update-memory",
                "memory_update",
                (
                    '{"memory_id":"'
                    + self.memory_id[:10]
                    + '","content":"解析器应先规范化输入, 再执行边界检查。"}'
                ),
            )
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        yield TextDelta("经验已更新。")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class VerifiedMemoryUpdateTransport:
    name = "verified-memory-update"

    def __init__(self, memory_id: str) -> None:
        self.memory_id = memory_id
        self.step = 0
        self.assessment_calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        block = request.messages[-1].content[0]
        if isinstance(block, TextBlock) and "should_store" in block.text:
            self.assessment_calls += 1
            yield TextDelta(
                '{"should_store":true,"reason":"更新现有经验",'
                '"problem":"解析器顺序错误","solution":"先规范化再检查边界",'
                '"applicability":"输入解析器","title":"解析器经验",'
                '"summary":"先规范化再检查边界",'
                '"body":"解析器应先规范化输入, 再执行边界检查。","tags":["parser"]}'
            )
            yield ModelCompleted(StopReason.STOP)
            return
        self.step += 1
        if self.step == 1:
            yield ToolCallDelta(
                "write-parser",
                "write_file",
                '{"path":"parser_fix.py","content":"VALUE = 1\\n"}',
            )
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        if self.step == 2:
            yield ToolCallDelta("check-parser", "shell", '{"command":"ruff check parser_fix.py"}')
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        if self.step == 3:
            yield ToolCallDelta(
                "update-memory",
                "memory_update",
                (
                    '{"memory_id":"'
                    + self.memory_id[:10]
                    + '","content":"解析器应先规范化输入, 再执行边界检查。"}'
                ),
            )
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        yield TextDelta("解析器已修复, 经验已更新。")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


async def test_confirmed_user_memory_is_recalled_across_sessions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = RecordingTransport()
    async with Windcode.open({}, state_root=tmp_path / "state", workspace=workspace) as client:
        client.register_transport("recording", "model", transport, primary=True)
        first = client.start_run(RunRequest("我喜欢先运行 focused tests", workspace))
        events = [event async for event in first]
        await first.result()
        activated_event = next(
            event
            for event in events
            if isinstance(event, MemoryEvent) and event.action == "activated"
        )
        assert activated_event.memory_id is not None
        memory = await client.get_memory(activated_event.memory_id)
        assert memory.status is MemoryStatus.ACTIVE

        second = client.start_run(RunRequest("我喜欢什么？", workspace))  # noqa: RUF001
        second_events = [event async for event in second]
        await second.result()

    assert any(
        isinstance(event, MemoryEvent) and event.action == "recalled" for event in second_events
    )
    assert "我喜欢先运行 focused tests" in transport.requests[-1].system_prompt


async def test_stable_name_is_saved_automatically_without_explicit_request(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = RecordingTransport()
    async with Windcode.open({}, state_root=tmp_path / "state", workspace=workspace) as client:
        client.register_transport("recording", "model", transport, primary=True)
        handle = client.start_run(RunRequest("我叫tingfeng347", workspace))
        events = [event async for event in handle]
        await handle.result()
        records = await client.list_memories(status=MemoryStatus.ACTIVE)

    assert len(records) == 1
    assert records[0].kind is MemoryKind.USER_PROFILE
    assert "tingfeng347" in records[0].body
    assert any(
        isinstance(event, MemoryEvent)
        and event.action == "activated"
        and event.details["policy"] == "stable_user_fact"
        for event in events
    )


async def test_model_can_write_explicit_memory_without_duplicate_extraction(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = MemoryWriteTransport()
    async with Windcode.open({}, state_root=tmp_path / "state", workspace=workspace) as client:
        client.register_transport("memory-write", "model", transport, primary=True)
        handle = client.start_run(RunRequest("给我记住, 我偏好先运行 focused tests", workspace))
        events: list[object] = []
        async for event in handle:
            events.append(event)
            if isinstance(event, ApprovalRequested):
                await handle.respond(ApprovalResponse(event.request_id, "allow_once"))
        await handle.result()
        records = await client.list_memories()

    assert len(records) == 1
    assert records[0].body == "用户偏好先运行 focused tests。"
    assert records[0].status is MemoryStatus.ACTIVE
    assert (
        sum(isinstance(event, MemoryEvent) and event.action == "activated" for event in events) == 1
    )


async def test_model_updates_experience_through_memory_tool_without_duplicate(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    async with Windcode.open({}, state_root=tmp_path / "state", workspace=workspace) as client:
        original = await client.create_memory_candidate(
            kind=MemoryKind.EXPERIENCE,
            scope=MemoryScope.PROJECT,
            title="解析器经验",
            summary="先校验再规范化",
            body="解析器应先校验再规范化。",
        )
        original = await client.confirm_memory(original.memory_id)
        transport = MemoryUpdateTransport(original.memory_id)
        client.register_transport("memory-update", "model", transport, primary=True)
        handle = client.start_run(RunRequest("修复解析器并整理已有经验", workspace))
        events: list[object] = []
        async for event in handle:
            events.append(event)
            if isinstance(event, ApprovalRequested):
                await handle.respond(ApprovalResponse(event.request_id, "allow_once"))
        await handle.result()
        records = await client.list_memories()

    assert len(records) == 1
    assert records[0].memory_id == original.memory_id
    assert records[0].version == original.version + 1
    assert records[0].body == "解析器应先规范化输入, 再执行边界检查。"
    assert any(
        isinstance(event, MemoryEvent)
        and event.action == "updated"
        and event.memory_id == original.memory_id
        for event in events
    )


async def test_verified_tool_update_is_not_reextracted_or_duplicated(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AppConfig(sandbox=SandboxConfig(preset="danger_full_access"))
    async with Windcode.open(config, state_root=tmp_path / "state", workspace=workspace) as client:
        original = await client.create_memory_candidate(
            kind=MemoryKind.EXPERIENCE,
            scope=MemoryScope.PROJECT,
            title="解析器经验",
            summary="先校验再规范化",
            body="解析器应先校验再规范化。",
        )
        original = await client.confirm_memory(original.memory_id)
        transport = VerifiedMemoryUpdateTransport(original.memory_id)
        client.register_transport("verified-memory-update", "model", transport, primary=True)
        handle = client.start_run(RunRequest("修复解析器并更新相关经验", workspace))
        async for event in handle:
            if isinstance(event, ApprovalRequested):
                await handle.respond(ApprovalResponse(event.request_id, "allow_once"))
        await handle.result()
        records = await client.list_memories()

    assert len(records) == 1
    assert records[0].memory_id == original.memory_id
    assert records[0].body == "解析器应先规范化输入, 再执行边界检查。"
    assert records[0].evidence == ("ruff check parser_fix.py (exit 0)",)
    assert transport.assessment_calls == 0


async def test_disabled_memory_has_no_storage_or_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = RecordingTransport()
    config = {"memory": {"enabled": False}}
    async with Windcode.open(config, state_root=tmp_path / "state", workspace=workspace) as client:
        client.register_transport("recording", "model", transport, primary=True)
        handle = client.start_run(RunRequest("记住这个秘密以外的偏好", workspace))
        events = [event async for event in handle]
        await handle.result()
    assert not any(isinstance(event, MemoryEvent) for event in events)
    assert not (tmp_path / "state" / "memory").exists()
    assert not {
        "memory_search",
        "memory_list",
        "memory_get",
        "memory_delete",
        "memory_update",
        "memory_write",
    } & {tool.name for tool in transport.requests[0].tools}
    assert "长期记忆已禁用或不可用" in transport.requests[0].system_prompt


async def test_explicit_experience_activates_without_execution_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = RecordingTransport()
    async with Windcode.open({}, state_root=tmp_path / "state", workspace=workspace) as client:
        client.register_transport("recording", "model", transport, primary=True)
        handle = client.start_run(
            RunRequest("记住一条经验: 检查代码规范时先运行 ruff check", workspace)
        )
        events = [event async for event in handle]
        await handle.result()
        active = await client.list_memories(status=MemoryStatus.ACTIVE)

    assert len(active) == 1
    assert active[0].kind is MemoryKind.EXPERIENCE
    assert active[0].activation is MemoryActivation.SEARCH
    assert any(
        isinstance(event, MemoryEvent)
        and event.action == "activated"
        and event.memory_kind == MemoryKind.EXPERIENCE.value
        for event in events
    )


async def test_routine_verification_without_changes_creates_no_experience(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "clean.py").write_text("VALUE = 1\n", encoding="utf-8")
    transport = ExperienceTransport(change_file=False)
    config = AppConfig(sandbox=SandboxConfig(preset="danger_full_access"))
    async with Windcode.open(config, state_root=tmp_path / "state", workspace=workspace) as client:
        client.register_transport("experience", "model", transport, primary=True)
        handle = client.start_run(RunRequest("检查代码规范", workspace))
        async for event in handle:
            if isinstance(event, ApprovalRequested):
                await handle.respond(ApprovalResponse(event.request_id, "allow_once"))
        await handle.result()
        records = await client.list_memories()

    assert records == ()
    assert transport.assessment_calls == 0


async def test_reusable_verified_change_creates_active_experience(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = ExperienceTransport(change_file=True)
    config = AppConfig(sandbox=SandboxConfig(preset="danger_full_access"))
    async with Windcode.open(config, state_root=tmp_path / "state", workspace=workspace) as client:
        client.register_transport("experience", "model", transport, primary=True)
        handle = client.start_run(RunRequest("新增模块并检查规范", workspace))
        async for event in handle:
            if isinstance(event, ApprovalRequested):
                await handle.respond(ApprovalResponse(event.request_id, "allow_once"))
        await handle.result()
        records = await client.list_memories(status=MemoryStatus.ACTIVE)

    assert len(records) == 1
    assert records[0].kind is MemoryKind.EXPERIENCE
    assert records[0].evidence == ("ruff check . (exit 0)",)
    assert transport.assessment_calls == 1


async def test_successful_external_action_without_verified_change_creates_no_experience(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = ExternalActionExperienceTransport()
    config = AppConfig(sandbox=SandboxConfig(preset="danger_full_access"))
    async with Windcode.open(config, state_root=tmp_path / "state", workspace=workspace) as client:
        client.register_transport("experience", "model", transport, primary=True)
        handle = client.start_run(RunRequest("安装 create-ppt 插件", workspace))
        async for event in handle:
            if isinstance(event, ApprovalRequested):
                await handle.respond(ApprovalResponse(event.request_id, "allow_once"))
        await handle.result()
        records = await client.list_memories(status=MemoryStatus.ACTIVE)

    assert records == ()
    assert transport.assessment_calls == 0


async def test_natural_language_memory_request_uses_read_only_memory_tool(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = ActiveMemoryQueryTransport()
    async with Windcode.open({}, state_root=tmp_path / "state", workspace=workspace) as client:
        client.register_transport("active-memory-query", "model", transport, primary=True)
        memory = await client.create_memory_candidate(
            kind=MemoryKind.USER_PROFILE,
            scope=MemoryScope.USER,
            title="语言偏好",
            summary="用户偏好中文",
            body="使用中文回答。",
        )
        await client.confirm_memory(memory.memory_id)
        handle = client.start_run(RunRequest("在长期记忆中看看", workspace))
        events = [event async for event in handle]
        result = await handle.result()

    assert result.final_text == "长期记忆中保存了语言偏好。"
    assert len(transport.requests) == 2
    assert any(
        isinstance(event, MemoryEvent) and event.action == "listed" and event.details["count"] == 1
        for event in events
    )


async def test_memory_uses_selected_state_root_and_filters_current_project(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    workspace = tmp_path / "workspace"
    other_workspace = tmp_path / "other"
    workspace.mkdir()
    other_workspace.mkdir()
    source = MemoryService(state_root, workspace)
    user = await source.create_candidate(
        kind=MemoryKind.USER_PROFILE,
        scope=MemoryScope.USER,
        title="Language preference",
        summary="Use Chinese",
        body="Use Chinese for answers.",
    )
    project = await source.create_candidate(
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        title="Current architecture",
        summary="Runtime owns orchestration",
        body="Keep orchestration in runtime.",
    )
    other = await MemoryService(state_root, other_workspace).create_candidate(
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        title="Other architecture",
        summary="Other project fact",
        body="This belongs to another project.",
    )

    async with Windcode.open({}, state_root=state_root, workspace=workspace) as client:
        assert client.memory_service is not None
        assert client.memory_service.store.root == state_root / "memory"
        visible = await client.list_memories()

    assert {record.memory_id for record in visible} == {user.memory_id, project.memory_id}
    assert (await source.store.get(user.memory_id)).memory_id == user.memory_id
    assert (await source.store.get(project.memory_id)).memory_id == project.memory_id
    assert (await source.store.get(other.memory_id)).memory_id == other.memory_id
