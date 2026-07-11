from pathlib import Path

import pytest

from windcode.cli import parse_options, resolve_config, run
from windcode.config import PermissionMode


def test_parses_explicit_security_and_session_options(tmp_path: Path) -> None:
    options = parse_options(
        [
            str(tmp_path),
            "--model",
            "provider",
            "--resume",
            "session",
            "--permission-mode",
            "full_access",
            "--no-sandbox",
        ]
    )
    assert options.workspace == tmp_path
    assert options.model == "provider"
    assert options.resume_session == "session"
    assert options.permission_mode is PermissionMode.FULL_ACCESS
    assert options.sandbox_enabled is False


def test_cli_overrides_project_configuration(tmp_path: Path) -> None:
    project = tmp_path / ".windcode"
    project.mkdir()
    (project / "config.toml").write_text('[permission]\nmode = "plan"\n[sandbox]\nenabled = true\n')
    options = parse_options([str(tmp_path), "--permission-mode", "accept_edits", "--no-sandbox"])
    config = resolve_config(options)
    assert config.permission.mode is PermissionMode.ACCEPT_EDITS
    assert not config.sandbox.enabled


def test_missing_workspace_returns_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run([str(tmp_path / "missing")]) == 2
    assert "workspace is not a directory" in capsys.readouterr().err


def test_help_exits_successfully() -> None:
    with pytest.raises(SystemExit) as raised:
        parse_options(["--help"])
    assert raised.value.code == 0
