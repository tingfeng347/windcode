import json
from pathlib import Path

from windcode.sandbox import SandboxPolicy, SandboxPreset, SeatbeltSandbox, WindowsSandbox


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


def test_windows_helper_exposes_native_remediation(tmp_path: Path) -> None:
    helper = tmp_path / "windcode-sandbox"
    helper.write_text(
        "#!/bin/sh\nprintf '%s\\n' '"
        + json.dumps(
            {
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
        )
        + "'\n"
    )
    helper.chmod(0o755)

    sandbox = WindowsSandbox(tmp_path, helper=str(helper))

    assert not sandbox.status.available
    assert sandbox.status.remediation == "windcode sandbox setup"
