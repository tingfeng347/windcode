import json
from pathlib import Path

import pytest

from windcode.cli import run


def test_cli_install_reload_list_and_disable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "extensions" / "complete_plugin"
    workspace = tmp_path / "workspace"
    config_dir = workspace / ".windcode"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[storage]\nproject_state_root = ".windcode/state"\n'
        f'user_storage_root = "{(tmp_path / "home").as_posix()}"\n'
        "[extensions]\nenabled = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))

    assert run(("extensions", "install", str(fixture), "--workspace", str(workspace))) == 0
    capsys.readouterr()
    assert run(("extensions", "disable", "plugin:complete", "--workspace", str(workspace))) == 0
    capsys.readouterr()
    assert run(("extensions", "reload", "--workspace", str(workspace))) == 0
    capsys.readouterr()
    assert run(("extensions", "list", "--workspace", str(workspace), "--json")) == 0
    records = json.loads(capsys.readouterr().out)

    plugin_records = [record for record in records if record["source"]["plugin_id"] is not None]
    assert {record["source"]["plugin_id"] for record in plugin_records} == {"complete"}
    assert not any(record["enabled"] for record in plugin_records)

    assert (
        run(
            (
                "extensions",
                "enable",
                "plugin:complete",
                "--workspace",
                str(workspace),
                "--json",
            )
        )
        == 0
    )
    changed = json.loads(capsys.readouterr().out)
    assert changed == {"changed": True, "diagnostics": [], "reload_required": True}


def test_cli_requires_targets_for_stateful_operations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(("extensions", "enable", "--workspace", str(tmp_path))) == 2
    assert "TARGET is required" in capsys.readouterr().err


def test_cli_distinguishes_unknown_extension_from_argument_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / ".windcode"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text("[extensions]\nenabled = true\n", encoding="utf-8")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    assert run(("extensions", "inspect", "plugin:missing", "--workspace", str(tmp_path))) == 3
    assert "unknown extension" in capsys.readouterr().err
