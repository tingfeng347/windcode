import pytest
from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.widgets import Markdown, Static

from windcode.domain.events import (
    MemoryEvent,
    ModelStarted,
    ReasoningStatus,
    RunCompleted,
    TextDeltaEvent,
    ToolFinished,
    ToolProgress,
    ToolStarted,
)
from windcode.domain.tools import ToolResult
from windcode.tui.widgets import MessageStream, ToolBlock
from windcode.types import RunResult


class StreamApp(App[None]):
    def compose(self) -> ComposeResult:
        yield MessageStream(id="chat-area")


class ToolApp(App[None]):
    def __init__(self, event: ToolStarted) -> None:
        super().__init__()
        self.event = event

    def compose(self) -> ComposeResult:
        yield ToolBlock(self.event)


def event(event_id: str, text: str) -> TextDeltaEvent:
    return TextDeltaEvent(
        event_id=event_id,
        session_id="session",
        run_id="run",
        turn=1,
        text=text,
    )


@pytest.mark.asyncio
async def test_message_stream_merges_incremental_text_into_one_ai_row() -> None:
    app = StreamApp()
    async with app.run_test(size=(80, 24)) as pilot:
        stream = app.query_one("#chat-area", MessageStream)
        await stream.begin_run()
        await stream.apply_event(event("one", "第一段"))
        await stream.apply_event(event("two", "和第二段"))
        await pilot.pause()

        messages = [message for message in stream.query(Static) if message.has_class("ai-message")]
        assert len(messages) == 1
        assert "第一段和第二段" in str(messages[0].content)
        assert messages[0].region.width >= 76


@pytest.mark.asyncio
async def test_message_stream_renders_completed_response_as_markdown() -> None:
    app = StreamApp()
    async with app.run_test(size=(80, 24)) as pilot:
        stream = app.query_one("#chat-area", MessageStream)
        await stream.begin_run()
        await stream.apply_event(event("table", "| 名称 | 说明 |\n| --- | --- |\n| src | 源码 |"))
        await stream.apply_event(
            RunCompleted(
                event_id="complete",
                session_id="session",
                run_id="run",
                turn=1,
                result=RunResult(status="completed", final_text=""),
            )
        )
        await pilot.pause()

        markdown = stream.query_one(Markdown)
        prefix = stream.query_one(".ai-prefix", Static)
        assert prefix.region.y == markdown.region.y
        assert list(stream.query("MarkdownTable"))


@pytest.mark.asyncio
async def test_message_stream_confirms_automatic_stable_memory() -> None:
    app = StreamApp()
    async with app.run_test(size=(80, 24)) as pilot:
        stream = app.query_one("#chat-area", MessageStream)
        await stream.apply_event(
            MemoryEvent(
                event_id="memory",
                session_id="session",
                run_id="run",
                turn=1,
                action="activated",
                memory_id="memory-id",
                memory_kind="user_profile",
                scope="user",
                status="active",
                details={"policy": "stable_user_fact"},
            )
        )
        await pilot.pause()

        confirmation = stream.query_one(".system-message", Static)
        assert "已自动保存长期记忆" in str(confirmation.content)


@pytest.mark.asyncio
async def test_reasoning_deltas_update_only_one_live_spinner() -> None:
    app = StreamApp()
    async with app.run_test(size=(80, 24)) as pilot:
        stream = app.query_one("#chat-area", MessageStream)
        await stream.begin_run()
        for index, text in enumerate(("用户", "打了个", "招呼", "。", "我需要", "先")):
            await stream.apply_event(
                ReasoningStatus(
                    event_id=str(index),
                    session_id="session",
                    run_id="run",
                    turn=1,
                    status=text,
                )
            )
        await pilot.pause()

        assert len(list(stream.query("#spinner-live"))) == 1
        assert not list(stream.query(".system-message"))
        assert "推理状态" not in " ".join(str(widget.content) for widget in stream.query(Static))

        await stream.finish_run()
        await pilot.pause()
        assert not list(stream.query("#spinner-live"))
        assert len(list(stream.query(".thinking-done"))) == 1
        assert "本轮耗时" in str(stream.query_one(".thinking-done", Static).content)


@pytest.mark.asyncio
async def test_live_status_stays_after_tool_blocks_and_in_latest_ai_row() -> None:
    app = StreamApp()
    async with app.run_test(size=(80, 24)) as pilot:
        stream = app.query_one("#chat-area", MessageStream)
        await stream.begin_run()
        await stream.apply_event(
            ToolStarted(
                event_id="tool",
                session_id="session",
                run_id="run",
                turn=1,
                call_id="call",
                tool_name="shell",
                arguments={},
            )
        )
        await stream.mount_in_ai_row(Static("工具输出"))

        spinner = stream.query_one("#spinner-live", Static)
        assert spinner.parent is not None
        assert spinner.parent.children[-1] is spinner

        await stream.apply_event(
            ModelStarted(
                event_id="model",
                session_id="session",
                run_id="run",
                turn=1,
                model="test",
            )
        )
        await stream.apply_event(event("answer", "后续回复"))
        await pilot.pause()

        latest_row = list(stream.query(".ai-row"))[-1]
        assert spinner.parent is latest_row
        assert latest_row.children[-1] is spinner
        assert "处理中" in str(spinner.content)


@pytest.mark.asyncio
async def test_approval_wait_is_excluded_from_thinking_time() -> None:
    timestamps = iter((10.0, 12.0, 17.0, 20.0))
    stream = MessageStream(clock=lambda: next(timestamps))

    await stream.begin_run()
    stream.pause_thinking("approval")
    stream.resume_thinking("approval")

    assert stream.thinking_seconds == 5.0


@pytest.mark.asyncio
async def test_message_stream_does_not_repeat_previous_turn() -> None:
    app = StreamApp()
    async with app.run_test(size=(80, 24)):
        stream = app.query_one("#chat-area", MessageStream)

        await stream.add_user_message("你好")
        await stream.begin_run()
        await stream.apply_event(event("first-a", "第一轮"))
        await stream.apply_event(event("first-b", "回复"))
        await stream.apply_event(
            RunCompleted(
                event_id="first-complete",
                session_id="session",
                run_id="first-run",
                turn=1,
                result=RunResult(status="completed", final_text="第一轮回复"),
            )
        )

        await stream.add_user_message("好的")
        await stream.begin_run()
        await stream.apply_event(event("second-a", "第二轮"))
        await stream.apply_event(event("second-b", "回复"))

        completed = list(stream.query(Markdown).filter(".ai-message"))
        streaming = [message for message in stream.query(Static) if message.has_class("ai-message")]
        assert len(completed) == 1
        assert len(streaming) == 1
        completed_text = " ".join(str(part.content) for part in completed[0].query(Static))
        assert "第一轮回复" in completed_text
        assert "第二轮回复" in str(streaming[0].content)
        assert "第一轮回复" not in str(streaming[0].content)


@pytest.mark.asyncio
async def test_tool_block_tracks_result_metadata() -> None:
    started = ToolStarted(
        event_id="start",
        session_id="session",
        run_id="run",
        turn=1,
        call_id="call",
        tool_name="shell",
        arguments={"command": "pytest -q 'tests with spaces'"},
    )
    app = ToolApp(started)
    async with app.run_test() as pilot:
        block = app.query_one(ToolBlock)
        block.finish(
            ToolFinished(
                event_id="finish",
                session_id="session",
                run_id="run",
                turn=1,
                call_id="call",
                result=ToolResult("passed", data={"exit_code": 0}),
            )
        )
        await pilot.pause()
        assert "退出码 0" in str(block.title)
        assert "<0.01 秒" in str(block.title)
        assert "bash:" in str(block.content)
        assert "pytest -q 'tests with spaces'" in str(block.content)


@pytest.mark.asyncio
async def test_tool_block_treats_command_and_progress_as_plain_text() -> None:
    command = "printf '%s\\n' \"[/home/tingfeng/code/windcode']\""
    message = "running [/home/tingfeng/code/windcode']\n"
    started = ToolStarted(
        event_id="start",
        session_id="session",
        run_id="run",
        turn=1,
        call_id="call",
        tool_name="shell",
        arguments={"command": command},
    )
    app = ToolApp(started)
    async with app.run_test() as pilot:
        block = app.query_one(ToolBlock)
        block.progress(
            ToolProgress(
                event_id="progress",
                session_id="session",
                run_id="run",
                turn=1,
                call_id="call",
                message=message,
            )
        )
        await pilot.pause()
        assert command in str(block.content)
        assert message in str(block.content)
        assert isinstance(block.content, RichText)
        assert any(span.style == "bold cyan" for span in block.content.spans)
