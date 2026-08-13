from pathlib import Path

import pytest

from windcode.config import (
    ConfigError,
    PermissionMode,
    default_user_config_path,
    ensure_user_config,
    load_config,
)


def test_ensure_user_config_creates_defaults_with_private_permissions(tmp_path: Path) -> None:
    target = ensure_user_config(tmp_path / "windcode" / "config.toml")

    assert target.exists()
    assert target.stat().st_mode & 0o777 == 0o600
    content = target.read_text(encoding="utf-8")
    assert "[memory]" in content
    assert "[extensions]" in content
    assert "dashscope-web-search" not in content
    config = load_config(tmp_path, user_file=target)
    assert config.memory.enabled
    assert config.sandbox.network_enabled
    assert config.extensions.enabled
    assert config.extensions.mcp_servers == {}


def test_ensure_user_config_preserves_explicit_existing_settings(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text(
        '[permission]\nmode = "plan"\n[extensions]\nenabled = false\n',
        encoding="utf-8",
    )

    assert ensure_user_config(target) == target
    config = load_config(tmp_path, user_file=target)
    assert config.permission.mode is PermissionMode.PLAN
    assert not config.extensions.enabled
    assert config.extensions.mcp_servers == {}


def test_ensure_user_config_migrates_existing_file_with_extension_defaults(
    tmp_path: Path,
) -> None:
    target = tmp_path / "config.toml"
    target.write_text(
        'primary_provider = "local"\n'
        '[providers.local]\nprotocol = "openai_responses"\n'
        'model = "model"\napi_key_env = "LOCAL_API_KEY"\n',
        encoding="utf-8",
    )

    ensure_user_config(target)

    content = target.read_text(encoding="utf-8")
    config = load_config(tmp_path, user_file=target)
    assert config.primary_provider == "local"
    assert config.providers["local"].model == "model"
    assert config.extensions.enabled
    assert config.extensions.mcp_servers == {}
    assert config.memory.enabled
    assert config.sandbox.network_enabled
    assert "dashscope-web-search" not in content


def test_layers_are_deep_merged_in_precedence_order(tmp_path: Path) -> None:
    user = tmp_path / "user.toml"
    user.write_text(
        '[budgets]\nmax_model_steps = 10\nmax_tool_calls = 20\n[permission]\nmode = "plan"\n'
    )
    project = tmp_path / "project.toml"
    project.write_text("[budgets]\nmax_model_steps = 15\n")

    config = load_config(
        tmp_path,
        user_file=user,
        project_file=project,
        overrides={"permission": {"mode": "accept_edits"}},
    )

    assert config.budgets.max_model_steps == 15
    assert config.budgets.max_tool_calls == 20
    assert config.permission.mode is PermissionMode.ACCEPT_EDITS


def test_parse_error_contains_source_path(tmp_path: Path) -> None:
    broken = tmp_path / "broken.toml"
    broken.write_text("not = [valid")

    with pytest.raises(ConfigError, match=str(broken)):
        load_config(tmp_path, explicit_file=broken)


def test_explicit_missing_file_is_an_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(tmp_path, explicit_file=missing)


def test_project_state_root_loads_from_storage_config(tmp_path: Path) -> None:
    project = tmp_path / "config.toml"
    project.write_text('[storage]\nproject_state_root = ".windcode/state"\n', encoding="utf-8")
    config = load_config(tmp_path, project_file=project)
    assert config.storage.project_state_root == ".windcode/state"
    assert config.storage.user_storage_root == "~/.windcode"


def test_user_storage_root_can_be_configured(tmp_path: Path) -> None:
    project = tmp_path / "config.toml"
    project.write_text(
        '[storage]\nuser_storage_root = "~/.local/state/windcode/state"\n', encoding="utf-8"
    )
    config = load_config(tmp_path, project_file=project)
    assert config.storage.user_storage_root == "~/.local/state/windcode/state"


def test_default_user_config_is_inside_unified_storage_root() -> None:
    assert default_user_config_path() == Path.home() / ".windcode" / "config.toml"


def test_user_config_is_not_reloaded_as_project_config_for_home_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "home"
    user = workspace / ".windcode" / "config.toml"
    user.parent.mkdir(parents=True)
    user.write_text(
        "[extensions]\nenabled=true\n"
        "[extensions.mcp_servers.user-server]\n"
        'transport="streamable_http"\nurl="https://example.test/mcp"\n',
        encoding="utf-8",
    )

    config = load_config(workspace, user_file=user)

    assert config.extensions.project_mcp_servers == frozenset()
