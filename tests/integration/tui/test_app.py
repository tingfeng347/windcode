import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from time import monotonic
from typing import cast

import pytest
from rich.text import Text as RichText
from textual.content import Content
from textual.css.query import NoMatches
from textual.widgets import Button, Markdown, OptionList, Select, Static, Switch

from windcode import Windcode
from windcode.auth import CredentialStoreError
from windcode.config import (
    AppConfig,
    PermissionMode,
    ProviderConfig,
    ProviderProtocol,
    SandboxConfig,
)
from windcode.config.models import ExtensionConfig, McpStdioConfig
from windcode.domain.events import RunRequest
from windcode.domain.messages import TextBlock, ToolResultBlock
from windcode.domain.models import (
    ModelCompleted,
    ModelEvent,
    ModelRequest,
    StopReason,
    TextDelta,
    ToolCallDelta,
)
from windcode.memory import MemoryKind, MemoryScope
from windcode.tui import WindcodeApp
from windcode.tui.widgets import (
    ApprovalWidget,
    ChatInput,
    CommandMenu,
    MemoryManager,
    MessageStream,
    ProviderManager,
    QuestionWidget,
    WelcomeView,
)


class EchoTransport:
    name = "echo"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        prompt = next(
            block.text
            for message in reversed(request.messages)
            for block in message.content
            if isinstance(block, TextBlock)
        )
        yield TextDelta(f"回复:{prompt}")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class BrokenCredentialStore:
    def get(self, credential_id: str) -> str | None:
        del credential_id
        raise CredentialStoreError("Windcode 凭据文件格式无效")

    def set(self, credential_id: str, secret: str) -> None:
        del credential_id, secret

    def delete(self, credential_id: str) -> None:
        del credential_id


class ShellTransport:
    name = "shell"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if request.messages[-1].role.value == "user":
            yield ToolCallDelta("shell", "shell", '{"command":"printf ok"}')
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        yield TextDelta("完成")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class QueuedTransport:
    name = "queued"

    def __init__(self) -> None:
        self.release_first = asyncio.Event()
        self.prompts: list[str] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        prompt = next(
            block.text
            for message in reversed(request.messages)
            for block in message.content
            if isinstance(block, TextBlock)
        )
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            await self.release_first.wait()
        yield TextDelta(f"回复:{prompt}")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class AskQuestionTransport:
    name = "ask-question"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.tool_output = ""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            yield ToolCallDelta(
                "question",
                "ask_user",
                (
                    '{"questions":[{"id":"strategy","prompt":"选择方案",'
                    '"options":["方案 A","方案 B"]}]}'
                ),
            )
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        result = request.messages[-1].content[0]
        assert isinstance(result, ToolResultBlock)
        self.tool_output = result.content
        yield TextDelta("已采用方案 B。")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_new_session_shows_welcome_and_accepts_status_command(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(80, 24)) as pilot:
        assert app.query_one("#chat-input", ChatInput).has_focus
        assert app.ui_mode == "welcome"
        assert app.query_one("#welcome-view", WelcomeView).display
        assert not app.query_one("#chat-area", MessageStream).display

        await pilot.press("/", "s", "t", "a", "t", "u", "s", "enter")
        await pilot.pause()

        notice = app.query_one("#welcome-notice", Static)
        assert "会话: 新会话" in str(notice.content)
        assert "委派: explicit" in str(notice.content)
        assert not app.query_one("#command-menu", CommandMenu).is_open


@pytest.mark.asyncio
async def test_ask_question_selection_reaches_next_model_request(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    transport = AskQuestionTransport()
    async with app.run_test(size=(80, 24)) as pilot:
        app.client.register_transport("ask-question", "model", transport, primary=True)
        app.query_one("#chat-input", ChatInput).insert("请让我选择方案")
        await pilot.press("enter")
        while not list(app.query(QuestionWidget)):
            await pilot.pause(0.01)
        question = app.query_one(QuestionWidget)
        deadline = monotonic() + 30
        while True:
            try:
                select = cast(Select[str], question.query_one(Select))
                break
            except NoMatches:
                if monotonic() >= deadline:
                    pytest.fail("question choices were not mounted")
                await pilot.pause(0.01)
        while not select.has_focus:
            if monotonic() >= deadline:
                pytest.fail("question choice did not receive focus")
            await pilot.pause(0.01)

        await pilot.press("enter", "down", "down", "enter")
        await pilot.pause()
        await pilot.click("#question-submit")
        while app.handle is None or not app.handle.done:
            await pilot.pause(0.01)

    assert "方案 B" in transport.tool_output


@pytest.mark.asyncio
async def test_missing_provider_prompts_for_configuration_without_exiting(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(100, 32)) as pilot:
        notice = app.query_one("#welcome-notice", Static)
        assert "尚未配置模型 Provider" in str(notice.content)

        prompt = app.query_one("#chat-input", ChatInput)
        prompt.insert("分析这个项目")
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ProviderManager)
        assert app.handle is None
        assert not list(app.query(Static).filter(".user-message"))
        assert app.is_running


@pytest.mark.asyncio
async def test_provider_without_default_prompts_for_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WINDCODE_TEST_API_KEY", "test-key")
    config = AppConfig(
        providers={
            "available": ProviderConfig(
                protocol=ProviderProtocol.OPENAI_COMPATIBLE,
                model="model",
                base_url="https://example.invalid/v1",
                api_key_env="WINDCODE_TEST_API_KEY",
            )
        }
    )
    app = WindcodeApp(config, workspace=tmp_path, state_root=tmp_path / "state")

    async with app.run_test(size=(100, 32)) as pilot:
        assert app.client.transport_registry.aliases == ("available",)
        assert not app.client.can_resolve_model()

        prompt = app.query_one("#chat-input", ChatInput)
        prompt.insert("分析这个项目")
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ProviderManager)
        assert app.handle is None
        assert app.is_running


@pytest.mark.asyncio
async def test_invalid_provider_credentials_do_not_exit_tui(tmp_path: Path) -> None:
    config = AppConfig(
        providers={
            "broken": ProviderConfig(
                protocol=ProviderProtocol.OPENAI_RESPONSES,
                model="model",
                credential_id="broken",
            )
        },
        primary_provider="broken",
    )
    app = WindcodeApp(
        config,
        workspace=tmp_path,
        state_root=tmp_path / "state",
        credential_store=BrokenCredentialStore(),
    )

    async with app.run_test(size=(100, 32)) as pilot:
        notice = app.query_one("#welcome-notice", Static)
        assert "模型 Provider 加载失败" in str(notice.content)
        assert "凭据文件格式无效" in str(notice.content)

        prompt = app.query_one("#chat-input", ChatInput)
        prompt.insert("分析这个项目")
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ProviderManager)
        assert app.handle is None
        assert app.is_running


@pytest.mark.asyncio
async def test_required_mcp_startup_failure_blocks_first_reply(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "extensions": {
                "enabled": True,
                "mcp_servers": {
                    "blocked": {
                        "transport": "streamable_http",
                        "url": "https://example.test/mcp",
                        "required": True,
                    }
                },
            },
            "sandbox": {"network_enabled": False},
        }
    )
    app = WindcodeApp(config, workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(100, 32)) as pilot:
        transport = EchoTransport()
        app.client.register_transport("echo", "model", transport, primary=True)
        prompt = app.query_one("#chat-input", ChatInput)
        prompt.insert("继续运行")
        await pilot.press("enter")
        for _ in range(100):
            errors = tuple(app.query(".error-message").results(Static))
            if errors and app.handle is not None and app.handle.done:
                break
            await pilot.pause(0.02)

        replies = list(app.query(Markdown).filter(".ai-message"))
        errors = " ".join(
            str(message.content) for message in app.query(".error-message").results(Static)
        )
        assert not replies
        assert transport.calls == 0
        assert "required MCP startup blocked by: blocked" in errors
        assert "Check configuration, credentials, network policy" in errors
        assert app.client.mcp_startup_status.failed_servers == ("blocked",)
        assert app.is_running
        assert app.handle is not None and app.handle.done


@pytest.mark.asyncio
async def test_startup_explains_untrusted_project_mcp(tmp_path: Path) -> None:
    config = AppConfig(
        extensions=ExtensionConfig(
            enabled=True,
            mcp_servers={"project": McpStdioConfig(command="never-started")},
            project_mcp_servers=frozenset({"project"}),
        )
    )
    app = WindcodeApp(config, workspace=tmp_path, state_root=tmp_path / "state")

    async with app.run_test(size=(100, 32)):
        notice = app.query_one("#welcome-notice", Static)
        content = str(notice.content)
        assert "MCP" in content
        assert "1 个未信任" in content
        assert "/extensions" in content
        assert "按 T" in content


@pytest.mark.asyncio
async def test_memory_command_opens_manager_and_persists_enabled_switch(tmp_path: Path) -> None:
    config_file = tmp_path / ".windcode" / "config.toml"
    app = WindcodeApp(
        AppConfig(),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        config_file=config_file,
    )
    async with app.run_test(size=(80, 24)) as pilot:
        await app.client.create_memory_candidate(
            kind=MemoryKind.USER_PROFILE,
            scope=MemoryScope.USER,
            title="编程偏好",
            summary="我喜欢编程",
            body="我喜欢编程",
        )
        await pilot.press("/", "m", "e", "m", "o", "r", "y", "enter")
        await pilot.pause()
        manager = app.screen
        assert isinstance(manager, MemoryManager)
        forget = manager.query_one("#memory-forget", Button)
        close = manager.query_one("#memory-close", Button)
        assert forget.region.bottom <= manager.size.height
        assert close.region.bottom <= manager.size.height
        manager.query_one("#memory-list", OptionList).action_first()
        await pilot.pause()
        option = manager.query_one("#memory-list", OptionList).highlighted_option
        assert option is not None
        assert str(option.prompt) == "我喜欢编程"
        details = str(manager.query_one("#memory-details", Static).content)
        assert details.count("我喜欢编程") == 1
        switch = manager.query_one("#memory-enabled", Switch)
        assert switch.value
        switch.toggle()
        await pilot.pause()
        assert not app.config.memory.enabled
        assert "enabled = false" in config_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_shift_tab_cycles_permission_modes_and_updates_ui(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(80, 24)) as pilot:
        prompt = app.query_one("#chat-input", ChatInput)

        expected_modes = (
            ("accept_edits", "自动编辑", "green"),
            ("full_access", "完全授权", "red"),
            ("plan", "计划", "yellow"),
            ("default", "默认", "dim"),
        )

        for mode, label, style in expected_modes:
            await pilot.press("shift+tab")
            await pilot.pause()
            content = app.query_one("#mode-label", Static).content
            welcome_content = app.query_one("#welcome-context", Static).content
            assert isinstance(content, RichText)
            assert isinstance(welcome_content, RichText)
            assert app.permission_mode == mode
            assert label in str(content)
            assert content.spans[-1].style == style
            assert label in str(welcome_content)
            assert welcome_content.spans[2].style == style

        assert prompt.has_focus
        assert not [
            message
            for message in app.query(".system-message")
            if "权限模式:" in str(message.render())
        ]


@pytest.mark.asyncio
async def test_welcome_logo_is_static_with_multiple_colors(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(100, 30)) as pilot:
        logo = app.query_one("#welcome-logo", Static)
        first = logo.render()
        assert isinstance(first, Content)
        first_styles = tuple(str(span.style) for span in first.spans)
        await pilot.pause(0.15)
        second = logo.render()
        assert isinstance(second, Content)

        assert first_styles == tuple(str(span.style) for span in second.spans)
        colors = {str(span.style) for span in second.spans}
        assert len(colors) >= 4


@pytest.mark.asyncio
async def test_resumed_session_uses_compact_chat_layout(tmp_path: Path) -> None:
    app = WindcodeApp(
        AppConfig(), workspace=tmp_path, state_root=tmp_path / "state", session_id="existing"
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-area", MessageStream)
        prompt = app.query_one("#chat-input", ChatInput)
        status = app.query_one("#status-bar")

        assert chat.region.width >= 116
        assert chat.region.bottom <= prompt.region.y
        assert prompt.region.bottom <= status.region.y
        assert app.query_one("#title-bar").region.height == 1
        assert not app.query_one("#welcome-view", WelcomeView).display
        assert app.ui_mode == "chat"
        assert app.query_one("#mode-label", Static)
        assert app.query_one("#model-label", Static)
        sandbox_label = str(app.query_one("#sandbox-label", Static).content)
        assert sandbox_label == "沙箱: 开启 · 委派: 显式"
        assert "/" not in sandbox_label


@pytest.mark.asyncio
async def test_narrow_layout_keeps_input_and_status_separate(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(40, 24)) as pilot:
        await pilot.pause()
        prompt = app.query_one("#chat-input", ChatInput)
        status = app.query_one("#status-bar")

        assert prompt.region.bottom <= status.region.y
        assert app.has_class("narrow")
        assert prompt.region.width == 39


@pytest.mark.asyncio
async def test_new_command_returns_to_welcome_mode(tmp_path: Path) -> None:
    app = WindcodeApp(
        AppConfig(), workspace=tmp_path, state_root=tmp_path / "state", session_id="existing"
    )
    async with app.run_test(size=(80, 24)) as pilot:
        prompt = app.query_one("#chat-input", ChatInput)
        prompt.focus()
        await pilot.press("/", "n", "e", "w", "enter")
        await pilot.pause()

        assert app.ui_mode == "welcome"
        assert app.session_id is None
        assert app.query_one("#welcome-view", WelcomeView).display
        assert prompt.has_focus


@pytest.mark.asyncio
async def test_slash_menu_is_above_prompt_in_real_app(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(80, 24)) as pilot:
        prompt = app.query_one("#chat-input", ChatInput)
        await pilot.press("/")
        await pilot.pause()

        menu = app.query_one("#command-menu", CommandMenu)
        status = app.query_one("#status-bar")
        assert menu.is_open
        assert menu.region.bottom <= prompt.region.y
        assert prompt.region.bottom <= status.region.y
        assert menu.region.width == prompt.region.width


@pytest.mark.asyncio
async def test_resumed_tui_turn_does_not_render_previous_reply(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(80, 24)) as pilot:
        app.client.register_transport("echo", "model", EchoTransport(), primary=True)
        prompt = app.query_one("#chat-input", ChatInput)

        prompt.insert("第一轮")
        await pilot.press("enter")
        assert app.ui_mode == "chat"
        assert not app.query_one("#welcome-view", WelcomeView).display
        while app.handle is None or not app.handle.done:
            await pilot.pause(0.01)
        await pilot.pause()

        prompt.insert("第二轮")
        await pilot.press("enter")
        while not app.handle.done:
            await pilot.pause(0.01)
        await pilot.pause()

        replies = list(app.query(Markdown).filter(".ai-message"))
        reply_text = [
            " ".join(str(part.content) for part in reply.query(Static)) for reply in replies
        ]
        assert len(replies) == 2
        assert "回复:第一轮" in reply_text[0]
        assert "回复:第二轮" in reply_text[1]
        assert "回复:第一轮" not in reply_text[1]


@pytest.mark.asyncio
async def test_prompts_submitted_during_run_are_processed_in_fifo_order(tmp_path: Path) -> None:
    transport = QueuedTransport()
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(80, 24)) as pilot:
        app.client.register_transport("queued", "model", transport, primary=True)
        prompt = app.query_one("#chat-input", ChatInput)

        for value in ("第一条", "第二条", "第三条"):
            prompt.insert(value)
            await pilot.press("enter")
            await pilot.pause()

        assert transport.prompts == ["第一条"]
        assert list(app.prompt_queue) == ["第二条", "第三条"]
        assert "队列 2" in str(app.query_one("#mode-label", Static).content)

        transport.release_first.set()
        deadline = monotonic() + 30
        while monotonic() < deadline:
            if (
                transport.prompts == ["第一条", "第二条", "第三条"]
                and app.handle is not None
                and app.handle.done
                and not app.prompt_queue
            ):
                break
            await pilot.pause(0.01)
        else:
            pytest.fail(
                "queued prompts did not finish: "
                f"processed={transport.prompts!r}, queued={list(app.prompt_queue)!r}, "
                f"handle_done={app.handle is not None and app.handle.done}"
            )

        assert transport.prompts == ["第一条", "第二条", "第三条"]
        users = [str(message.content) for message in app.query(Static).filter(".user-message")]
        assert len(users) == 3
        assert all(
            rendered.endswith(prompt)
            for rendered, prompt in zip(users, transport.prompts, strict=True)
        )


@pytest.mark.asyncio
async def test_escape_requires_two_presses_to_interrupt_active_run(tmp_path: Path) -> None:
    transport = QueuedTransport()
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(80, 24)) as pilot:
        app.client.register_transport("queued", "model", transport, primary=True)
        prompt = app.query_one("#chat-input", ChatInput)
        prompt.insert("等待中的任务")
        await pilot.press("enter")
        await pilot.pause()

        assert app.handle is not None
        handle = app.handle
        await pilot.press("escape")
        await pilot.pause()
        assert not handle.done
        assert "再次 Esc 中断" in str(app.query_one("#mode-label", Static).content)

        await pilot.press("escape")
        for _ in range(100):
            if handle.done:
                break
            await pilot.pause(0.01)
        assert handle.done
        assert (await handle.result()).status == "cancelled"


@pytest.mark.asyncio
async def test_opening_existing_session_replays_visible_conversation(tmp_path: Path) -> None:
    state = tmp_path / "state"
    async with Windcode.open(state_root=state) as client:
        client.register_transport("echo", "model", EchoTransport(), primary=True)
        await client.start_run(RunRequest("历史问题", tmp_path, session_id="session")).result()

    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=state, session_id="session")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        users = [message for message in app.query(".user-message") if isinstance(message, Static)]
        replies = list(app.query(Markdown).filter(".ai-message"))
        assert len(users) == 1
        assert len(replies) == 1
        assert "历史问题" in str(users[0].content)
        reply_text = " ".join(str(part.content) for part in replies[0].query(Static))
        assert "回复:历史问题" in reply_text


@pytest.mark.asyncio
async def test_idle_compact_command_is_usable(tmp_path: Path) -> None:
    state = tmp_path / "state"
    async with Windcode.open(state_root=state) as client:
        client.register_transport("echo", "model", EchoTransport(), primary=True)
        await client.start_run(RunRequest("历史问题", tmp_path, session_id="session")).result()

    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=state, session_id="session")
    async with app.run_test(size=(100, 30)) as pilot:
        prompt = app.query_one("#chat-input", ChatInput)
        prompt.focus()
        prompt.insert("/compact")
        await pilot.press("enter")
        await pilot.pause()
        assert app.compact_next_run


@pytest.mark.asyncio
async def test_input_regains_focus_after_approved_agent_run(tmp_path: Path) -> None:
    app = WindcodeApp(
        AppConfig(sandbox=SandboxConfig(preset="danger_full_access")),
        workspace=tmp_path,
        state_root=tmp_path / "state",
    )
    async with app.run_test(size=(80, 24)) as pilot:
        app.client.register_transport("shell", "model", ShellTransport(), primary=True)
        prompt = app.query_one("#chat-input", ChatInput)
        prompt.insert("执行命令")
        await pilot.press("enter")

        deadline = monotonic() + 30
        while not list(app.query(ApprovalWidget)):
            if monotonic() >= deadline:
                pytest.fail("approval widget was not shown")
            await pilot.pause(0.01)

        approval = app.query_one(ApprovalWidget)
        for _ in range(100):
            if approval.has_focus:
                break
            await pilot.pause(0.01)
        approval_content = str(approval.query_one("#approval-content", Static).content)
        shell = "PowerShell" if os.name == "nt" else "bash"
        assert f"{shell}: printf ok" in RichText.from_markup(approval_content).plain
        await pilot.press("down", "enter")
        while app.handle is None or not app.handle.done:
            await pilot.pause(0.01)
        deadline = monotonic() + 30
        while not prompt.has_focus:
            if monotonic() >= deadline:
                pytest.fail("chat input did not regain focus")
            await pilot.pause(0.01)

        assert prompt.has_focus


@pytest.mark.asyncio
async def test_permission_mode_can_cycle_while_agent_is_waiting_for_approval(
    tmp_path: Path,
) -> None:
    app = WindcodeApp(
        AppConfig(sandbox=SandboxConfig(preset="danger_full_access")),
        workspace=tmp_path,
        state_root=tmp_path / "state",
    )
    async with app.run_test(size=(80, 24)) as pilot:
        app.client.register_transport("shell", "model", ShellTransport(), primary=True)
        prompt = app.query_one("#chat-input", ChatInput)
        prompt.insert("执行命令")
        await pilot.press("enter")

        deadline = monotonic() + 30
        while not list(app.query(ApprovalWidget)):
            if monotonic() >= deadline:
                pytest.fail("approval widget was not shown")
            await pilot.pause(0.01)

        approval = app.query_one(ApprovalWidget)
        for _ in range(100):
            if approval.has_focus:
                break
            await pilot.pause(0.01)
        assert app.handle is not None
        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.permission_mode == PermissionMode.ACCEPT_EDITS.value
        assert app.handle.permission_mode is PermissionMode.ACCEPT_EDITS
        assert "运行中" in str(app.query_one("#mode-label", Static).content)
        assert not [
            message
            for message in app.query(".system-message")
            if "不能切换权限模式" in str(message.render())
        ]

        await pilot.press("down", "down", "enter")
        while not app.handle.done:
            await pilot.pause(0.01)
