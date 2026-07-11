import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from windcode.domain.events import (
    ReasoningStatus,
    RunCompleted,
    TextDeltaEvent,
    ToolFinished,
    ToolStarted,
)
from windcode.domain.tools import ToolResult
from windcode.tui.widgets import MessageStream, ToolBlock
from windcode.types import RunResult


class StreamApp(App[None]):
    def compose(self) -> ComposeResult:
        yield MessageStream(id="chat-area")


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

        messages = [message for message in stream.query(Static) if message.has_class("ai-message")]
        assert len(messages) == 2
        assert "第一轮回复" in str(messages[0].content)
        assert "第二轮回复" in str(messages[1].content)
        assert "第一轮回复" not in str(messages[1].content)


def test_tool_block_tracks_result_metadata() -> None:
    started = ToolStarted(
        event_id="start",
        session_id="session",
        run_id="run",
        turn=1,
        call_id="call",
        tool_name="shell",
        arguments={"command": "pytest"},
    )
    block = ToolBlock(started)
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
    assert "退出码 0" in str(block.title)
    assert "<0.01 秒" in str(block.title)
