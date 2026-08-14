from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from textual.containers import Horizontal, Vertical
from textual.pilot import Pilot
from textual.widgets import Button, Input, OptionList, Select, Static

from windcode.config import AppConfig, ProviderConfig, ProviderProtocol
from windcode.tui import WindcodeApp
from windcode.tui.widgets import ChatInput, ModelManager, ProviderManager


class MemoryCredentialStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, credential_id: str) -> str | None:
        return self.values.get(credential_id)

    def set(self, credential_id: str, secret: str) -> None:
        self.values[credential_id] = secret

    def delete(self, credential_id: str) -> None:
        self.values.pop(credential_id, None)


async def open_provider_manager(app: WindcodeApp, pilot: Pilot[None]) -> ProviderManager:
    prompt = app.query_one(ChatInput)
    prompt.insert("/model")
    await pilot.press("enter")
    await pilot.click("#model-manage")
    await pilot.pause()
    return cast(ProviderManager, app.screen)


@pytest.mark.asyncio
async def test_model_command_persists_secret_and_selects_profile(tmp_path: Path) -> None:
    store = MemoryCredentialStore()
    config_file = tmp_path / ".windcode" / "config.toml"
    app = WindcodeApp(
        AppConfig(),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        config_file=config_file,
        credential_store=store,
    )

    async with app.run_test(size=(110, 40)) as pilot:
        manager = await open_provider_manager(app, pilot)
        assert manager.query_one("#provider-api-key", Input).has_focus
        manager.query_one("#provider-alias", Input).value = "codex"
        manager.query_one("#provider-model", Input).value = "gpt-test"
        manager.query_one("#provider-api-key", Input).value = "stored-secret"
        await pilot.click("#provider-save")
        await pilot.pause()

        assert app.model == "codex"
        assert app.config.primary_provider == "codex"
        assert app.config.providers["codex"].credential_id == "codex"
        assert store.get("codex") == "stored-secret"
        content = config_file.read_text(encoding="utf-8")
        assert 'credential_id = "codex"' in content
        assert "stored-secret" not in content

        await pilot.click("#provider-close")
        await pilot.pause()
        picker = cast(ModelManager, app.screen)
        assert picker.query_one("#model-search", Input).has_focus
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.id == "_default"
        assert app.query_one(ChatInput).has_focus


@pytest.mark.asyncio
async def test_provider_manager_validates_compatible_base_url(tmp_path: Path) -> None:
    app = WindcodeApp(
        AppConfig(),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        credential_store=MemoryCredentialStore(),
    )
    async with app.run_test(size=(110, 40)) as pilot:
        manager = await open_provider_manager(app, pilot)
        manager.query_one("#provider-alias", Input).value = "local"
        manager.query_one("#provider-model", Input).value = "qwen"
        manager.query_one("#provider-base-url", Input).value = ""
        cast(
            Select[str], manager.query_one("#provider-protocol", Select)
        ).value = "openai_compatible"
        await pilot.click("#provider-save")
        await pilot.pause()

        error = str(manager.query_one("#provider-editor-error", Static).content)
        assert "必须填写 Base URL" in error
        assert app.config.providers == {}


@pytest.mark.asyncio
async def test_missing_key_is_saved_as_disconnected_provider(tmp_path: Path) -> None:
    config_file = tmp_path / ".windcode" / "config.toml"
    app = WindcodeApp(
        AppConfig(),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        config_file=config_file,
        credential_store=MemoryCredentialStore(),
    )
    async with app.run_test(size=(110, 40)) as pilot:
        manager = await open_provider_manager(app, pilot)
        manager.query_one("#provider-alias", Input).value = "missing"
        manager.query_one("#provider-model", Input).value = "model"
        manager.query_one("#provider-api-key-env", Input).value = "MISSING_MODEL_KEY"
        await pilot.click("#provider-save")
        await pilot.pause()

        assert app.config.providers["missing"].credential_id == "missing"
        assert config_file.exists()
        assert "missing" not in app.client.transport_registry.aliases


@pytest.mark.asyncio
async def test_sets_default_and_disconnects_provider(tmp_path: Path) -> None:
    profiles = {
        "first": ProviderConfig(
            protocol=ProviderProtocol.ANTHROPIC_MESSAGES,
            model="claude",
            credential_id="first",
        ),
        "second": ProviderConfig(
            protocol=ProviderProtocol.OPENAI_RESPONSES,
            model="gpt",
            credential_id="second",
        ),
    }
    store = MemoryCredentialStore({"first": "one", "second": "two"})
    app = WindcodeApp(
        AppConfig(providers=profiles, primary_provider="first", fallback_chain=("second",)),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        config_file=tmp_path / ".windcode" / "config.toml",
        credential_store=store,
    )
    async with app.run_test(size=(110, 40)) as pilot:
        manager = await open_provider_manager(app, pilot)
        manager.query_one("#provider-list", OptionList).highlighted = 1
        await pilot.click("#provider-default")
        await pilot.pause()

        assert app.config.primary_provider == "second"
        manager = cast(ProviderManager, app.screen)
        assert await pilot.click("#provider-delete")
        assert manager.query_one("#provider-confirm-delete", Button).display
        assert await pilot.click("#provider-confirm-delete")
        await pilot.pause()

        assert "second" not in app.config.providers
        assert store.get("second") is None
        assert app.config.primary_provider == "first"


@pytest.mark.asyncio
async def test_model_picker_searches_by_model_name(tmp_path: Path) -> None:
    profiles = {
        "codex": ProviderConfig(
            protocol=ProviderProtocol.OPENAI_RESPONSES,
            model="gpt-codex",
            credential_id="codex",
        ),
        "claude": ProviderConfig(
            protocol=ProviderProtocol.ANTHROPIC_MESSAGES,
            model="claude-sonnet",
            credential_id="claude",
        ),
    }
    store = MemoryCredentialStore({"codex": "one", "claude": "two"})
    app = WindcodeApp(
        AppConfig(providers=profiles, primary_provider="codex"),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        credential_store=store,
    )
    async with app.run_test(size=(80, 32)) as pilot:
        prompt = app.query_one(ChatInput)
        prompt.insert("/model")
        await pilot.press("enter")
        picker = cast(ModelManager, app.screen)
        picker.query_one("#model-search", Input).value = "sonnet"
        await pilot.pause()

        options = picker.query_one("#model-list", OptionList)
        assert options.option_count == 1
        assert options.get_option_at_index(0).id == "claude"


@pytest.mark.asyncio
async def test_arrow_keys_switch_provider_group_and_choose_model(tmp_path: Path) -> None:
    profiles = {
        "codex": ProviderConfig(
            protocol=ProviderProtocol.OPENAI_RESPONSES,
            model="gpt-codex",
            provider_id="openai",
            credential_id="codex",
        ),
        "ds": ProviderConfig(
            protocol=ProviderProtocol.OPENAI_COMPATIBLE,
            model="deepseek-chat",
            provider_id="deepseek",
            credential_id="ds",
            base_url="https://api.deepseek.com/v1",
        ),
    }
    store = MemoryCredentialStore({"codex": "one", "ds": "two"})
    app = WindcodeApp(
        AppConfig(providers=profiles, primary_provider="codex"),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        credential_store=store,
    )
    async with app.run_test(size=(80, 32)) as pilot:
        app.query_one(ChatInput).insert("/model")
        await pilot.press("enter")
        picker = cast(ModelManager, app.screen)

        await pilot.press("right", "right")
        await pilot.pause()
        assert "DeepSeek" in str(picker.query_one("#model-provider-tabs", Static).content)
        model_list = picker.query_one("#model-list", OptionList)
        model_list.highlighted = next(
            i
            for i in range(model_list.option_count)
            if model_list.get_option_at_index(i).id == "ds"
        )
        await pilot.press("enter")
        await pilot.pause()

        assert app.model == "ds"
        assert app.screen.id == "_default"


@pytest.mark.asyncio
async def test_builtin_provider_preset_fills_connection_fields(tmp_path: Path) -> None:
    app = WindcodeApp(
        AppConfig(),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        credential_store=MemoryCredentialStore(),
    )
    async with app.run_test(size=(80, 32)) as pilot:
        manager = await open_provider_manager(app, pilot)
        preset = cast(Select[str], manager.query_one("#provider-preset", Select))
        preset.value = "deepseek"
        await pilot.pause()

        assert manager.query_one("#provider-alias", Input).value == "deepseek"
        assert manager.query_one("#provider-api-key-env", Input).value == "DEEPSEEK_API_KEY"
        assert manager.query_one("#provider-base-url", Input).value == "https://api.deepseek.com/v1"
        assert (
            cast(Select[str], manager.query_one("#provider-protocol", Select)).value
            == "openai_compatible"
        )
        labels = tuple(
            str(label.content) for label in manager.query(".provider-field-label").results(Static)
        )
        assert labels == (
            "厂商模板",
            "API Key",
            "模型 ID",
            "配置别名",
            "接口协议",
            "Base URL",
            "环境变量",
        )


@pytest.mark.asyncio
async def test_provider_manager_uses_persistent_workspace_regions(tmp_path: Path) -> None:
    profiles = {
        "codex": ProviderConfig(
            protocol=ProviderProtocol.OPENAI_RESPONSES,
            model="gpt-codex",
            provider_id="openai",
            api_key_env="OPENAI_API_KEY",
            credential_id="codex",
        )
    }
    app = WindcodeApp(
        AppConfig(providers=profiles, primary_provider="codex"),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        credential_store=MemoryCredentialStore(),
    )
    async with app.run_test(size=(120, 42)) as pilot:
        manager = await open_provider_manager(app, pilot)
        await pilot.pause()

        assert manager.query_one("#provider-list", OptionList).display
        assert manager.query_one("#provider-editor", Vertical).display
        assert manager.query_one("#provider-inspector", Vertical).display
        assert "OpenAI Responses" in str(manager.query_one("#provider-details", Static).content)
        editor = manager.query_one("#provider-editor", Vertical)
        inspector = manager.query_one("#provider-inspector", Vertical)
        actions = manager.query_one("#provider-editor-actions", Horizontal)
        assert editor.region.contains_region(inspector.region)
        assert editor.region.contains_region(actions.region)
        assert inspector.region.bottom <= actions.region.y


@pytest.mark.asyncio
async def test_provider_models_are_loaded_and_first_id_is_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_fetch_model_ids(provider: ProviderConfig, api_key: str) -> tuple[str, ...]:
        assert provider.provider_id == "openai"
        assert api_key == "secret"
        return ("gpt-a", "gpt-b")

    monkeypatch.setattr("windcode.tui.app.fetch_model_ids", fake_fetch_model_ids)
    app = WindcodeApp(
        AppConfig(),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        credential_store=MemoryCredentialStore(),
    )
    async with app.run_test(size=(90, 36)) as pilot:
        manager = await open_provider_manager(app, pilot)
        manager.query_one("#provider-api-key", Input).value = "secret"
        await pilot.click("#provider-load-models")
        await pilot.pause()

        model_select = cast(Select[str], manager.query_one("#provider-model-options", Select))
        assert model_select.value == "gpt-a"
        assert manager.query_one("#provider-model", Input).value == "gpt-a"
        assert "已加载 2 个模型" in str(manager.query_one("#provider-editor-error", Static).content)


@pytest.mark.asyncio
async def test_empty_model_id_keeps_provider_editor_open(tmp_path: Path) -> None:
    app = WindcodeApp(
        AppConfig(),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        credential_store=MemoryCredentialStore(),
    )
    async with app.run_test(size=(90, 36)) as pilot:
        manager = await open_provider_manager(app, pilot)
        manager.query_one("#provider-model", Input).value = ""
        await pilot.click("#provider-save")
        await pilot.pause()

        assert manager.query_one("#provider-editor", Vertical).display
        assert "请填写模型 ID" in str(manager.query_one("#provider-editor-error", Static).content)


@pytest.mark.asyncio
async def test_empty_model_picker_can_connect_builtin_provider(tmp_path: Path) -> None:
    app = WindcodeApp(
        AppConfig(),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        credential_store=MemoryCredentialStore(),
    )
    async with app.run_test(size=(80, 32)) as pilot:
        app.query_one(ChatInput).insert("/model")
        await pilot.press("enter", "right", "down", "enter")
        await pilot.pause()

        manager = cast(ProviderManager, app.screen)
        assert cast(Select[str], manager.query_one("#provider-preset", Select)).value == "openai"
        assert manager.query_one("#provider-base-url", Input).value == "https://api.openai.com/v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [40, 80, 120])
async def test_model_dialogs_fit_terminal_width(tmp_path: Path, width: int) -> None:
    app = WindcodeApp(
        AppConfig(),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        credential_store=MemoryCredentialStore(),
    )
    async with app.run_test(size=(width, 32)) as pilot:
        manager = await open_provider_manager(app, pilot)
        await pilot.pause()
        save = manager.query_one("#provider-save", Button)
        cancel = manager.query_one("#provider-cancel", Button)
        dialog = manager.query_one("#provider-dialog", Vertical)
        editor = manager.query_one("#provider-editor", Vertical)
        form = manager.query_one("#provider-form", Vertical)
        actions = manager.query_one("#provider-editor-actions", Horizontal)
        assert abs(dialog.region.center[0] - app.screen.region.center[0]) <= 0.5
        assert abs(dialog.region.center[1] - app.screen.region.center[1]) <= 0.5
        assert editor.region.x <= form.region.x
        assert form.region.right <= editor.region.right
        assert editor.region.contains_region(actions.region)
        assert save.region.bottom <= app.screen.region.bottom
        assert cancel.region.right <= app.screen.region.right
