from pathlib import Path

import pytest

from windcode import sandbox as sandbox_module
from windcode.cli import parse_options, resolve_config, run
from windcode.config import PermissionMode, SandboxPreset
from windcode.web import server as web_server


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
    (project / "config.toml").write_text(
        '[permission]\nmode = "plan"\n[sandbox]\npreset = "workspace_write"\n'
    )
    options = parse_options([str(tmp_path), "--permission-mode", "accept_edits", "--no-sandbox"])
    config = resolve_config(options)
    assert config.permission.mode is PermissionMode.ACCEPT_EDITS
    assert not config.sandbox.enabled
    assert config.sandbox.preset == SandboxPreset.DANGER_FULL_ACCESS


def test_missing_workspace_returns_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run([str(tmp_path / "missing")]) == 2
    assert "workspace is not a directory" in capsys.readouterr().err


def test_help_exits_successfully() -> None:
    with pytest.raises(SystemExit) as raised:
        parse_options(["--help"])
    assert raised.value.code == 0


def test_windows_sandbox_setup_command_routes_to_native_helper(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sandbox_module,
        "setup_windows_sandbox",
        lambda: {"version": 1, "ready": True},
    )

    assert run(["sandbox", "setup", "--json"]) == 0
    assert '"ready": true' in capsys.readouterr().out


def test_web_command_routes_to_loopback_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, int, bool]] = []

    def record_server_start(workspace: Path, *, port: int, open_browser: bool) -> None:
        calls.append((workspace, port, open_browser))

    monkeypatch.setattr(
        web_server,
        "run_web_server",
        record_server_start,
    )

    assert run(["web", str(tmp_path), "--port", "9123", "--no-open"]) == 0
    assert calls == [(tmp_path.resolve(), 9123, False)]


def test_web_command_rejects_invalid_port(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(["web", str(tmp_path), "--port", "70000"]) == 2
    assert "port must be between 1 and 65535" in capsys.readouterr().err
