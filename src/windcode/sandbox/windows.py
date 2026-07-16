from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

from windcode.sandbox.models import (
    LaunchSpec,
    SandboxCapabilities,
    SandboxPolicy,
    SandboxPreset,
    SandboxStatus,
)

PROTOCOL_VERSION = 1


def find_windows_helper(helper: str | None = None) -> Path | None:
    bundled = Path(__file__).with_name("bin") / "windcode-sandbox.exe"
    located = helper or (str(bundled) if bundled.is_file() else shutil.which("windcode-sandbox"))
    return None if located is None else Path(located).resolve()


def setup_windows_sandbox(helper: str | None = None) -> dict[str, object]:
    executable = find_windows_helper(helper)
    if executable is None:
        raise RuntimeError("the native Windcode Windows sandbox helper is not installed")
    result = subprocess.run(
        (executable, "setup", "--json"),
        capture_output=True,
        text=True,
        timeout=120.0,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"helper exited with status {result.returncode}"
        raise RuntimeError(detail)
    decoded = cast(object, json.loads(result.stdout))
    if not isinstance(decoded, dict):
        raise RuntimeError("sandbox helper returned a non-object setup response")
    return {str(key): value for key, value in cast(dict[object, object], decoded).items()}


class WindowsSandbox:
    def __init__(self, workspace: Path, helper: str | None = None) -> None:
        self.workspace = workspace.expanduser().resolve()
        executable = find_windows_helper(helper)
        located = None if executable is None else str(executable)
        capabilities = SandboxCapabilities(False, False, False)
        ready = False
        warning: str | None = None
        remediation: str | None = None
        if located:
            try:
                result = subprocess.run(
                    (located, "status", "--json"),
                    capture_output=True,
                    text=True,
                    timeout=10.0,
                    check=True,
                )
                decoded = cast(object, json.loads(result.stdout))
                if not isinstance(decoded, dict):
                    raise ValueError("sandbox helper returned a non-object status")
                payload = cast(dict[object, object], decoded)
                if payload.get("version") != PROTOCOL_VERSION:
                    raise ValueError("sandbox helper protocol mismatch")
                raw_capabilities = payload.get("capabilities")
                if not isinstance(raw_capabilities, dict):
                    raise ValueError("sandbox helper did not report capabilities")
                capability_values = cast(dict[object, object], raw_capabilities)
                capabilities = SandboxCapabilities(
                    bool(capability_values.get("filesystem_isolation")),
                    bool(capability_values.get("network_isolation")),
                    bool(capability_values.get("process_isolation")),
                )
                ready = bool(payload.get("ready")) and all(
                    (
                        capabilities.filesystem_isolation,
                        capabilities.network_isolation,
                        capabilities.process_isolation,
                    )
                )
                warning = None if ready else str(payload.get("warning") or "helper is degraded")
                raw_remediation = payload.get("remediation")
                remediation = str(raw_remediation) if raw_remediation else None
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
                warning = f"Windows sandbox helper handshake failed: {exc}"
        self.status = SandboxStatus(
            ready,
            None if located is None else Path(located).resolve(),
            warning=warning
            or (None if located else "the Windcode Windows sandbox helper is not installed"),
            backend="windows-helper",
            capabilities=capabilities,
            remediation=(
                remediation
                if located
                else "Install a Windows wheel containing the native sandbox helper."
            ),
        )

    def prepare(
        self, command: tuple[str, ...], *, cwd: object, policy: SandboxPolicy
    ) -> LaunchSpec:
        if not isinstance(cwd, Path):
            raise TypeError("sandbox cwd must be a Path")
        if policy.preset is SandboxPreset.DANGER_FULL_ACCESS:
            return LaunchSpec(command, cwd, backend="none", sandboxed=False)
        if not self.status.available or self.status.executable is None:
            raise RuntimeError(self.status.warning or "Windows sandbox helper unavailable")
        request = {
            "version": PROTOCOL_VERSION,
            "command": list(command),
            "cwd": str(cwd),
            "workspace": str(self.workspace),
            "preset": policy.preset.value,
            "writable_roots": [str(path.resolve()) for path in policy.writable_roots],
            "network_enabled": policy.network_enabled,
            "parent_pid": os.getpid(),
        }
        encoded = json.dumps(request, separators=(",", ":"), ensure_ascii=True)
        wrapped = (str(self.status.executable), "run", "--request", encoded)
        return LaunchSpec(wrapped, cwd, backend="windows-helper", sandboxed=True)

    @staticmethod
    def classifies_denial(returncode: int | None, stderr: str) -> bool:
        return returncode == 77 and "WINDCODE_SANDBOX_DENIAL" in stderr
