import json
import subprocess
from pathlib import Path

import pytest

from windcode.sandbox import (
    SandboxPolicy,
    SandboxPreset,
    SeatbeltSandbox,
    WindowsSandbox,
    create_sandbox_backend,
)


def test_windows_disables_os_sandbox_instead_of_creating_native_backend(
    tmp_path: Path,
) -> None:
    backend, policy = create_sandbox_backend(
        tmp_path,
        platform="win32",
        preset=SandboxPreset.WORKSPACE_WRITE,
    )

    assert backend is None
    assert policy.preset is SandboxPreset.DANGER_FULL_ACCESS


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
        del args, kwargs
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
