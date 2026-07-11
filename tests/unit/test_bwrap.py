from pathlib import Path

import pytest

from windcode.sandbox import BubblewrapSandbox, SandboxStatus, detect_bubblewrap


def test_builds_isolated_arguments(tmp_path: Path) -> None:
    executable = tmp_path / "bwrap"
    status = SandboxStatus(True, executable)
    arguments = BubblewrapSandbox(tmp_path, status).wrap(("bash", "-lc", "pwd"))

    assert arguments[0] == str(executable)
    assert "--unshare-net" in arguments
    assert ("--bind", str(tmp_path), str(tmp_path)) == arguments[6:9]
    assert arguments[-4:] == ("--", "bash", "-lc", "pwd")


def test_network_can_be_explicitly_shared(tmp_path: Path) -> None:
    status = SandboxStatus(True, tmp_path / "bwrap")
    arguments = BubblewrapSandbox(tmp_path, status).wrap(("true",), allow_network=True)
    assert "--unshare-net" not in arguments


def test_missing_bwrap_reports_degradation(tmp_path: Path) -> None:
    status = detect_bubblewrap("definitely-not-installed-windcode-test")
    assert not status.available
    assert status.warning is not None
    with pytest.raises(RuntimeError, match="unavailable"):
        BubblewrapSandbox(tmp_path, status).wrap(("true",))
