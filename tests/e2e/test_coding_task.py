import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from windcode import Windcode
from windcode.config import (
    AppConfig,
    ContextConfig,
    ProviderConfig,
    ProviderProtocol,
    SandboxConfig,
)
from windcode.context import CHECKPOINT_SECTIONS
from windcode.domain.errors import ErrorCategory, WindcodeError
from windcode.domain.events import (
    AgentEventType,
    ApprovalRequested,
    ApprovalResponse,
    ContextCompacted,
    ModelFallback,
    ModelRetrying,
    RunCancelled,
    RunCompleted,
    RunRequest,
    TextDeltaEvent,
    ToolFinished,
    ToolStarted,
    UsageUpdated,
)
from windcode.domain.messages import Role, TextBlock, ToolResultBlock
from windcode.domain.models import (
    ModelCompleted,
    ModelEvent,
    ModelRequest,
    ModelUsage,
    StopReason,
    TextDelta,
    ToolCallDelta,
    Usage,
)
from windcode.sessions import SessionStore
from windcode.tui import WindcodeApp
from windcode.tui.widgets import ApprovalWidget, ChatInput, MessageStream


class CodingTaskTransport:
    name = "scripted"

    def __init__(self) -> None:
        self.step = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.step += 1
        yield ModelUsage(Usage(input_tokens=self.step, output_tokens=1))
        if self.step == 1:
            yield ToolCallDelta("read", "read_file", '{"path":"calc.py"}')
            yield ModelCompleted(StopReason.TOOL_USE)
        elif self.step == 2:
            previous = request.messages[-1].content[0]
            assert isinstance(previous, ToolResultBlock)
            assert "return 1" in previous.content
            yield ToolCallDelta(
                "write",
                "write_file",
                '{"path":"calc.py","content":"def answer():\\n    return 2\\n"}',
            )
            yield ModelCompleted(StopReason.TOOL_USE)
        elif self.step == 3:
            previous = request.messages[-1].content[0]
            assert isinstance(previous, ToolResultBlock)
            if previous.is_error:
                yield TextDelta("Write was denied; no file was changed.")
                yield ModelCompleted(StopReason.STOP)
                return
            yield ToolCallDelta("test", "shell", '{"command":"pytest -q"}')
            yield ModelCompleted(StopReason.TOOL_USE)
        else:
            previous = request.messages[-1].content[0]
            assert isinstance(previous, ToolResultBlock)
            assert "passed" in previous.content
            yield TextDelta("Fixed calc.py and verified the test suite.")
            yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class FailingTransport:
    name = "failing"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        self.calls += 1
        raise WindcodeError("temporary server failure", ErrorCategory.SERVER)
        yield TextDelta("")

    async def aclose(self) -> None:
        pass


class CompletingTransport:
    name = "completing"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        self.calls += 1
        yield TextDelta("Fallback model completed the task.")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class CompactingTaskTransport:
    name = "compacting"

    def __init__(self) -> None:
        self.compaction_calls = 0
        self.task_calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        last_block = request.messages[-1].content[0]
        if isinstance(last_block, TextBlock) and "结构化检查点" in last_block.text:
            self.compaction_calls += 1
            checkpoint = "\n".join(
                f"## {section}\nretained evidence" for section in CHECKPOINT_SECTIONS
            )
            yield TextDelta(checkpoint)
            yield ModelCompleted(StopReason.STOP)
            return

        self.task_calls += 1
        if self.task_calls == 1:
            assert any(
                message.role is Role.SYSTEM
                and isinstance(message.content[0], TextBlock)
                and "上下文检查点" in message.content[0].text
                for message in request.messages
            )
            yield ToolCallDelta(
                "write",
                "write_file",
                '{"path":"result.txt","content":"completed after compaction\\n"}',
            )
            yield ModelCompleted(StopReason.TOOL_USE)
            return

        previous = request.messages[-1].content[0]
        assert isinstance(previous, ToolResultBlock)
        assert not previous.is_error
        yield TextDelta("Compacted context retained enough state to finish.")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class SlowShellTransport:
    name = "slow-shell"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        self.calls += 1
        yield ToolCallDelta("slow", "shell", '{"command":"sleep 60"}')
        yield ModelCompleted(StopReason.TOOL_USE)

    async def aclose(self) -> None:
        pass


def make_project(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "calc.py").write_text("def answer():\n    return 1\n")
    (tmp_path / "test_calc.py").write_text(
        "from calc import answer\n\n\ndef test_answer():\n    assert answer() == 2\n"
    )


def event_semantics(event: AgentEventType) -> tuple[object, ...]:
    if isinstance(event, TextDeltaEvent):
        return (event.kind, event.text)
    if isinstance(event, ApprovalRequested):
        return (event.kind, event.risk, event.choices)
    if isinstance(event, ToolStarted):
        return (event.kind, event.tool_name, event.arguments)
    if isinstance(event, ToolFinished):
        return (
            event.kind,
            event.result.is_error,
            event.result.data.get("exit_code"),
        )
    if isinstance(event, UsageUpdated):
        return (
            event.kind,
            event.usage.input_tokens,
            event.usage.output_tokens,
        )
    if isinstance(event, RunCompleted):
        return (event.kind, event.result.status)
    return (event.kind,)


@pytest.mark.asyncio
async def test_successful_coding_task_has_real_change_and_verification(tmp_path: Path) -> None:
    make_project(tmp_path)
    config = AppConfig(sandbox=SandboxConfig(enabled=False))
    events: list[AgentEventType] = []
    async with Windcode.open(config, state_root=tmp_path / ".state") as client:
        client.register_transport("scripted", "model", CodingTaskTransport(), primary=True)
        handle = client.start_run(RunRequest("fix the failing test", tmp_path))
        async for event in handle:
            events.append(event)
            if isinstance(event, ApprovalRequested):
                await handle.respond(ApprovalResponse(event.request_id, "allow_once"))
        result = await handle.result()

    assert (tmp_path / "calc.py").read_text() == "def answer():\n    return 2\n"
    assert result.status == "completed"
    assert result.changed_files == ("calc.py",)
    assert result.verification == ("pytest -q (exit 0)",)
    assert isinstance(events[-1], RunCompleted)


@pytest.mark.asyncio
async def test_sdk_and_tui_success_paths_emit_same_event_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_workspace = tmp_path / "sdk"
    tui_workspace = tmp_path / "tui"
    make_project(sdk_workspace)
    make_project(tui_workspace)
    config = AppConfig(sandbox=SandboxConfig(enabled=False))

    sdk_events: list[AgentEventType] = []
    async with Windcode.open(config, state_root=tmp_path / "sdk-state") as client:
        client.register_transport("scripted", "model", CodingTaskTransport(), primary=True)
        handle = client.start_run(RunRequest("fix the failing test", sdk_workspace))
        async for event in handle:
            sdk_events.append(event)
            if isinstance(event, ApprovalRequested):
                await handle.respond(ApprovalResponse(event.request_id, "allow_once"))
        sdk_result = await handle.result()

    tui_events: list[AgentEventType] = []
    original_apply_event = MessageStream.apply_event

    async def record_event(stream: MessageStream, event: AgentEventType) -> None:
        tui_events.append(event)
        await original_apply_event(stream, event)

    monkeypatch.setattr(MessageStream, "apply_event", record_event)
    app = WindcodeApp(
        config,
        workspace=tui_workspace,
        state_root=tmp_path / "tui-state",
    )
    async with app.run_test(size=(100, 30)) as pilot:
        app.client.register_transport("scripted", "model", CodingTaskTransport(), primary=True)
        prompt = app.query_one("#chat-input", ChatInput)
        prompt.insert("fix the failing test")
        await pilot.press("enter")
        for _ in range(500):
            approval = list(app.query(ApprovalWidget))
            if approval:
                await pilot.press("enter")
            if app.handle is not None and app.handle.done:
                break
            await pilot.pause(0.01)
        else:
            pytest.fail("TUI coding task did not finish")
        await pilot.pause()
        assert app.handle is not None
        tui_result = await app.handle.result()

    assert sdk_result == tui_result
    assert [event_semantics(event) for event in sdk_events] == [
        event_semantics(event) for event in tui_events
    ]
    assert (sdk_workspace / "calc.py").read_text() == "def answer():\n    return 2\n"
    assert (tui_workspace / "calc.py").read_text() == "def answer():\n    return 2\n"


@pytest.mark.asyncio
async def test_denied_write_keeps_file_unchanged_and_run_continues(tmp_path: Path) -> None:
    make_project(tmp_path)
    config = AppConfig(sandbox=SandboxConfig(enabled=False))
    transport = CodingTaskTransport()
    async with Windcode.open(config, state_root=tmp_path / ".state") as client:
        client.register_transport("scripted", "model", transport, primary=True)
        handle = client.start_run(RunRequest("fix the failing test", tmp_path))
        async for event in handle:
            if isinstance(event, ApprovalRequested):
                await handle.respond(ApprovalResponse(event.request_id, "deny"))
        result = await handle.result()

    assert (tmp_path / "calc.py").read_text() == "def answer():\n    return 1\n"
    assert result.status == "unverified"


@pytest.mark.asyncio
async def test_fallback_retries_primary_then_completes_with_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WINDCODE_PRIMARY_TEST_KEY", "primary-key")
    monkeypatch.setenv("WINDCODE_BACKUP_TEST_KEY", "backup-key")
    config = AppConfig(
        providers={
            "primary": ProviderConfig(
                protocol=ProviderProtocol.OPENAI_COMPATIBLE,
                model="primary-model",
                api_key_env="WINDCODE_PRIMARY_TEST_KEY",
                base_url="http://127.0.0.1:1",
            ),
            "backup": ProviderConfig(
                protocol=ProviderProtocol.OPENAI_COMPATIBLE,
                model="backup-model",
                api_key_env="WINDCODE_BACKUP_TEST_KEY",
                base_url="http://127.0.0.1:1",
            ),
        },
        primary_provider="primary",
        fallback_chain=("backup",),
    )
    primary = FailingTransport()
    backup = CompletingTransport()
    events: list[AgentEventType] = []
    async with Windcode.open(config, state_root=tmp_path / ".state") as client:
        client.register_transport("primary", "primary-model", primary, replace_existing=True)
        client.register_transport("backup", "backup-model", backup, replace_existing=True)
        handle = client.start_run(RunRequest("finish through fallback", tmp_path))
        events = [event async for event in handle]
        result = await handle.result()

    assert primary.calls == 3
    assert backup.calls == 1
    assert len([event for event in events if isinstance(event, ModelRetrying)]) == 2
    assert len([event for event in events if isinstance(event, ModelFallback)]) == 1
    assert result.final_text == "Fallback model completed the task."


@pytest.mark.asyncio
async def test_compact_context_then_completes_followup_edit(tmp_path: Path) -> None:
    config = AppConfig(
        sandbox=SandboxConfig(enabled=False),
        context=ContextConfig(
            window_tokens=5_000,
            compaction_threshold=0.01,
            preserve_recent_turns=1,
        ),
    )
    transport = CompactingTaskTransport()
    events: list[AgentEventType] = []
    async with Windcode.open(config, state_root=tmp_path / ".state") as client:
        client.register_transport("compacting", "model", transport, primary=True)
        handle = client.start_run(RunRequest("x" * 2_000, tmp_path))
        async for event in handle:
            events.append(event)
            if isinstance(event, ApprovalRequested):
                await handle.respond(ApprovalResponse(event.request_id, "allow_once"))
        result = await handle.result()

    assert transport.compaction_calls >= 1
    assert any(isinstance(event, ContextCompacted) for event in events)
    assert (tmp_path / "result.txt").read_text() == "completed after compaction\n"
    assert result.final_text == "Compacted context retained enough state to finish."


@pytest.mark.asyncio
async def test_cancel_running_shell_records_cancelled_without_replay(tmp_path: Path) -> None:
    state = tmp_path / ".state"
    transport = SlowShellTransport()
    events: list[AgentEventType] = []
    config = AppConfig(sandbox=SandboxConfig(enabled=False))
    async with Windcode.open(config, state_root=state) as client:
        client.register_transport("slow-shell", "model", transport, primary=True)
        handle = client.start_run(
            RunRequest("run the slow command", tmp_path, session_id="cancel-session")
        )
        async for event in handle:
            events.append(event)
            if isinstance(event, ApprovalRequested):
                await handle.respond(ApprovalResponse(event.request_id, "allow_once"))
            elif isinstance(event, ToolStarted):
                await asyncio.sleep(0.05)
                await handle.cancel()
        result = await handle.result()

    store = SessionStore.open(state / "sessions", "cancel-session")
    interrupted = store.recover_interrupted_side_effects()
    assert transport.calls == 1
    assert result.status == "cancelled"
    assert isinstance(events[-1], RunCancelled)
    assert [record.payload["call_id"] for record in interrupted] == ["slow"]
