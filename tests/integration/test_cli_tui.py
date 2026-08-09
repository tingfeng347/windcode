from pathlib import Path

import pytest

from windcode.cli import run
from windcode.tui import WindcodeApp


def test_cli_constructs_and_runs_tui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fake_run(self: object) -> None:
        nonlocal called
        del self
        called = True

    monkeypatch.setattr("windcode.tui.WindcodeApp.run", fake_run)

    assert run([str(tmp_path), "--permission-mode", "plan", "--no-sandbox"]) == 0
    assert called


def test_invalid_provider_configuration_opens_tui_in_recovery_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('primary_provider = "missing"\n', encoding="utf-8")
    startup_error: str | None = None

    def fake_run(self: WindcodeApp) -> None:
        nonlocal startup_error
        startup_error = self.client.model_startup_error

    monkeypatch.setattr("windcode.tui.WindcodeApp.run", fake_run)

    assert run([str(tmp_path), "--config", str(config_file)]) == 0
    assert startup_error is not None
    assert "unknown providers" in startup_error
