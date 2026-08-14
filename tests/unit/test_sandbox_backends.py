import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from windcode.sandbox import (
    SandboxPolicy,
    SandboxPreset,
    SeatbeltSandbox,
    WindowsSandbox,
    setup_windows_sandbox,
)


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS-only")
def test_seatbelt_profile_limits_writes_and_network(tmp_path: Path) -> None:
    sandbox = SeatbeltSandbox(tmp_path)
    sandbox.status = sandbox.status.__class__(
        True,
        Path("/usr/bin/sandbox-exec"),
        backend="seatbelt",
        capabilities=sandbox.status.capabilities,
    )

    spec = sandbox.prepare(
        ("bash", "-lc", "true"),
        cwd=tmp_path,
        policy=SandboxPolicy(SandboxPreset.WORKSPACE_WRITE, (), False),
    )

    profile = spec.command[2]
    assert "(deny default)" in profile
    assert f'(subpath "{tmp_path}")' in profile
    assert "(deny network*)" in profile


def test_windows_helper_without_complete_capabilities_fails_closed(tmp_path: Path) -> None:
    sandbox = WindowsSandbox(tmp_path, helper="definitely-missing-windcode-helper")

    assert not sandbox.status.available
    assert sandbox.status.warning is not None


def test_windows_helper_exposes_native_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "windcode-sandbox"
    helper.touch()
    response = {
        "version": 1,
        "ready": False,
        "capabilities": {
            "filesystem_isolation": False,
            "network_isolation": False,
            "process_isolation": True,
        },
        "warning": "administrator initialization is required",
        "remediation": "windcode sandbox setup",
    }

    def helper_response(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        return subprocess.CompletedProcess((str(helper),), 0, json.dumps(response), "")

    monkeypatch.setattr(
        "windcode.sandbox.windows.subprocess.run",
        helper_response,
    )

    sandbox = WindowsSandbox(tmp_path, helper=str(helper))

    assert not sandbox.status.available
    assert sandbox.status.remediation == "windcode sandbox setup"


def test_windows_helper_rejects_truthy_non_boolean_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "windcode-sandbox"
    helper.touch()
    response = {
        "version": 1,
        "ready": True,
        "capabilities": {
            "filesystem_isolation": "false",
            "network_isolation": "false",
            "process_isolation": "false",
        },
    }

    def helper_response(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess((str(helper),), 0, json.dumps(response), "")

    monkeypatch.setattr(
        "windcode.sandbox.windows.subprocess.run",
        helper_response,
    )

    assert not WindowsSandbox(tmp_path, helper=str(helper)).status.available


def test_windows_helper_timeout_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "windcode-sandbox"
    helper.touch()

    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise subprocess.TimeoutExpired(str(helper), 30)

    monkeypatch.setattr("windcode.sandbox.windows.subprocess.run", timeout)

    sandbox = WindowsSandbox(tmp_path, helper=str(helper))

    assert not sandbox.status.available
    assert sandbox.status.warning is not None
    assert "timed out" in sandbox.status.warning


def test_setup_windows_sandbox_skips_when_already_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "windcode-sandbox"
    helper.touch()
    invoked: list[tuple[str, ...]] = []

    def fake_invoke(path: Path, *arguments: str) -> dict[str, object]:
        invoked.append((path.name, *arguments))
        return {"version": 1, "ready": True}

    monkeypatch.setattr("windcode.sandbox.windows._invoke_helper", fake_invoke)

    result = setup_windows_sandbox(helper=str(helper))

    assert result["ready"] is True
    assert invoked == [("windcode-sandbox", "status", "--json")]


def test_setup_windows_sandbox_runs_setup_directly_when_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "windcode-sandbox"
    helper.touch()
    statuses: Iterator[dict[str, object]] = iter(
        (
            dict[str, object](version=1, ready=False),
            dict[str, object](version=1, ready=True),
        )
    )
    invoked: list[tuple[str, ...]] = []

    def fake_invoke(path: Path, *arguments: str) -> dict[str, object]:
        invoked.append((path.name, *arguments))
        return next(statuses)

    monkeypatch.setattr("windcode.sandbox.windows._invoke_helper", fake_invoke)
    monkeypatch.setattr("windcode.sandbox.windows._is_elevated", lambda: True)

    result = setup_windows_sandbox(helper=str(helper))

    assert result["ready"] is True
    assert invoked[0] == ("windcode-sandbox", "status", "--json")
    assert invoked[1] == ("windcode-sandbox", "setup", "--json")


def test_setup_windows_sandbox_elevates_when_not_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "windcode-sandbox"
    helper.touch()
    statuses: Iterator[dict[str, object]] = iter(
        (
            dict[str, object](version=1, ready=False),
            dict[str, object](version=1, ready=True),
        )
    )
    invoked: list[tuple[str, ...]] = []
    elevated_scripts: list[str] = []

    def fake_invoke(path: Path, *arguments: str) -> dict[str, object]:
        invoked.append((path.name, *arguments))
        return next(statuses)

    def fake_run(argv: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        elevated_scripts.append(str(argv))
        return subprocess.CompletedProcess(("powershell.exe",), 0, "", "")

    monkeypatch.setattr("windcode.sandbox.windows._invoke_helper", fake_invoke)
    monkeypatch.setattr("windcode.sandbox.windows._is_elevated", lambda: False)
    monkeypatch.setattr("windcode.sandbox.windows.subprocess.run", fake_run)

    result = setup_windows_sandbox(helper=str(helper))

    assert result["ready"] is True
    assert invoked[0] == ("windcode-sandbox", "status", "--json")
    assert invoked[1] == ("windcode-sandbox", "status", "--json")
    assert len(elevated_scripts) == 1
    assert "Start-Process" in elevated_scripts[0]
    assert "-Verb RunAs" in elevated_scripts[0]


def test_setup_windows_sandbox_raises_when_elevation_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "windcode-sandbox"
    helper.touch()

    def fake_invoke(path: Path, *arguments: str) -> dict[str, object]:
        del path, arguments
        return {"version": 1, "ready": False}

    def fake_run(argv: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del argv, kwargs
        return subprocess.CompletedProcess(
            ("powershell.exe",), 1, "", "The operation was canceled by the user."
        )

    monkeypatch.setattr("windcode.sandbox.windows._invoke_helper", fake_invoke)
    monkeypatch.setattr("windcode.sandbox.windows._is_elevated", lambda: False)
    monkeypatch.setattr("windcode.sandbox.windows.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="canceled by the user"):
        setup_windows_sandbox(helper=str(helper))
