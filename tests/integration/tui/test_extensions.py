from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from textual.widgets import OptionList, Static

from windcode.config import AppConfig
from windcode.config.models import ExtensionConfig, McpStdioConfig
from windcode.domain.messages import TextBlock
from windcode.domain.models import ModelCompleted, ModelEvent, ModelRequest, StopReason, TextDelta
from windcode.tui import WindcodeApp
from windcode.tui.widgets import ChatInput, CommandMenu, ExtensionManager, WelcomeView


class CapturingTransport:
    name = "capture"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield TextDelta("done")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(40, 24), (80, 24), (120, 36)])
async def test_extensions_opens_a_manager_dialog(tmp_path: Path, size: tuple[int, int]) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=size) as pilot:
        await pilot.press(*"/extensions", "enter")
        await pilot.pause()

        manager = cast(ExtensionManager, app.screen)
        assert manager.region.width <= app.size.width
        assert manager.region.height <= app.size.height


@pytest.mark.asyncio
async def test_tui_extensions_uses_shared_sdk_state(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "extensions" / "complete_plugin"
    app = WindcodeApp(
        AppConfig(extensions=ExtensionConfig(enabled=True)),
        workspace=tmp_path,
        state_root=tmp_path / "state",
    )
    async with app.run_test(size=(80, 24)) as pilot:
        await app.client.install_extension(fixture, enable=True)
        await pilot.press(*"/extensions reload", "enter")
        await pilot.pause()

        notice = app.query_one("#welcome-view", WelcomeView).query_one("#welcome-notice", Static)
        assert "再次输入相同命令执行" in str(notice.content)
        await pilot.press(*"/extensions reload", "enter")
        await pilot.pause()

        notice = app.query_one("#welcome-view", WelcomeView).query_one("#welcome-notice", Static)
        assert "插件" in str(notice.content)
        assert "complete · 可用" in str(notice.content)
        assert "$review" in str(notice.content)
        assert any(
            record.enabled for record in await app.client.inspect_extension("plugin:complete")
        )


@pytest.mark.asyncio
async def test_plugin_skill_is_selected_with_dollar_menu_and_loads_sourced_context(
    tmp_path: Path,
) -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "extensions" / "complete_plugin"
    app = WindcodeApp(
        AppConfig(extensions=ExtensionConfig(enabled=True)),
        workspace=tmp_path,
        state_root=tmp_path / "state",
    )
    transport = CapturingTransport()
    async with app.run_test(size=(80, 24)) as pilot:
        app.client.register_transport("capture", "model", transport, primary=True)
        await app.client.install_extension(fixture, enable=True)
        await app.client.reload_extensions()

        prompt = app.query_one("#chat-input", ChatInput)
        prompt.insert("$rev")
        await pilot.pause()
        menu = app.query_one("#command-menu", CommandMenu)
        assert [item.name for item in menu.items] == ["review"]
        await pilot.press("tab")
        await pilot.pause()
        assert prompt.text == "$review "
        prompt.clear()

        prompt.insert("/rev")
        await pilot.pause()
        assert app.client.extension_commands() == ()
        prompt.clear()
        prompt.insert("$review inspect this")
        await pilot.press("enter")
        while app.handle is None or not app.handle.done:
            await pilot.pause(0.01)

    sourced = [
        message
        for message in transport.requests[0].messages
        if message.provider_metadata.get("extension_source") is not None
    ]
    assert sourced[0].provider_metadata["extension_source"].startswith("plugin:complete")
    assert any(
        "correctness risks" in block.text
        for block in sourced[0].content
        if isinstance(block, TextBlock)
    )


@pytest.mark.asyncio
async def test_extension_manager_inspects_and_toggles_a_plugin(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "extensions" / "complete_plugin"
    app = WindcodeApp(
        AppConfig(extensions=ExtensionConfig(enabled=True)),
        workspace=tmp_path,
        state_root=tmp_path / "state",
    )
    async with app.run_test(size=(100, 36)) as pilot:
        await app.client.install_extension(fixture, enable=True)
        await app.client.reload_extensions()

        prompt = app.query_one("#chat-input", ChatInput)
        prompt.insert("/extensions")
        await pilot.press("enter")
        await pilot.pause()

        manager = cast(ExtensionManager, app.screen)
        listing = manager.query_one("#extension-list", OptionList)
        option_ids = {option.id for option in listing.options}
        assert "plugin:complete/skill/review" not in option_ids
        assert "plugin:complete/mcp_server/analysis" not in option_ids
        plugin_index = next(
            index
            for index, option in enumerate(listing.options)
            if option.id == "plugin:complete/plugin/complete"
        )
        listing.highlighted = plugin_index
        await pilot.press("enter")
        await pilot.pause()
        details = str(manager.query_one("#extension-details", Static).content)
        assert "名称: complete" in details
        assert "技能: $review" in details
        assert "MCP: analysis" in details

        await pilot.press("space")
        await pilot.pause()
        records = await app.client.inspect_extension("plugin:complete")
        assert not all(record.enabled for record in records)


@pytest.mark.asyncio
async def test_extension_manager_toggles_configured_mcp_with_space(tmp_path: Path) -> None:
    app = WindcodeApp(
        AppConfig(
            extensions=ExtensionConfig(
                enabled=True,
                mcp_servers={"toggleable": McpStdioConfig(command="never-started")},
            )
        ),
        workspace=tmp_path,
        state_root=tmp_path / "state",
    )
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.press(*"/extensions", "enter")
        await pilot.pause()

        manager = cast(ExtensionManager, app.screen)
        listing = manager.query_one("#extension-list", OptionList)
        listing.highlighted = next(
            index
            for index, option in enumerate(listing.options)
            if option.id == "mcp_server:toggleable"
        )
        await pilot.press("space")
        await pilot.pause()

        record = (await app.client.inspect_extension("mcp_server:toggleable"))[0]
        assert not record.enabled
