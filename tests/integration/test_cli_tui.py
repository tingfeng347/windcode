from pathlib import Path

import pytest

from windcode.cli import run


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
