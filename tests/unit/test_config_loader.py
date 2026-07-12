from pathlib import Path

import pytest

from windcode.config import ConfigError, PermissionMode, load_config


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
    assert config.storage.user_storage_root is None


def test_user_storage_root_can_be_configured(tmp_path: Path) -> None:
    project = tmp_path / "config.toml"
    project.write_text(
        '[storage]\nuser_storage_root = "~/.local/state/windcode/state"\n', encoding="utf-8"
    )
    config = load_config(tmp_path, project_file=project)
    assert config.storage.user_storage_root == "~/.local/state/windcode/state"
