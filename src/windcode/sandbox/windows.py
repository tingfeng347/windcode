from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from windcode.sandbox.models import (
    LaunchSpec,
    SandboxCapabilities,
    SandboxPolicy,
    SandboxPreset,
    SandboxStatus,
)

DEFAULT_WINDOWS_SANDBOX_HELPER = "windcode-sandbox"


def _helper_path(helper: str) -> Path | None:
    located = shutil.which(helper)
    if located is None:
        candidate = Path(helper).expanduser()
        if not candidate.is_file():
            return None
        located = str(candidate)
    return Path(located).resolve()


def _invoke_helper(helper: Path, *arguments: str) -> dict[str, object]:
    try:
        completed = subprocess.run(
            (str(helper), *arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Windows sandbox helper timed out") from exc
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "helper failed"
        raise RuntimeError(message)
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Windows sandbox helper returned invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError("Windows sandbox helper response must be an object")
    return {str(key): item for key, item in cast(Mapping[object, object], value).items()}


def _capabilities(value: object) -> SandboxCapabilities:
    raw = (
        {str(key): item for key, item in cast(Mapping[object, object], value).items()}
        if isinstance(value, Mapping)
        else {}
    )
    return SandboxCapabilities(
        raw.get("filesystem_isolation") is True,
        raw.get("network_isolation") is True,
        raw.get("process_isolation") is True,
    )


def setup_windows_sandbox(
    helper: str = DEFAULT_WINDOWS_SANDBOX_HELPER,
) -> dict[str, object]:
    executable = _helper_path(helper)
    if executable is None:
        raise FileNotFoundError(f"Windows sandbox helper is unavailable: {helper}")
    return _invoke_helper(executable, "setup", "--json")


class WindowsSandbox:
    def __init__(
        self,
        workspace: Path,
        helper: str = DEFAULT_WINDOWS_SANDBOX_HELPER,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        executable = _helper_path(helper)
        if executable is None:
            self.status = SandboxStatus(
                False,
                None,
                "Windows sandbox helper is unavailable",
                "windows-native",
                remediation="Run `windcode sandbox setup` after installing windcode-sandbox.",
            )
            return
        try:
            response = _invoke_helper(executable, "status", "--json")
            capabilities = _capabilities(response.get("capabilities"))
            ready = (
                response.get("version") == 1
                and response.get("ready") is True
                and capabilities.filesystem_isolation
                and capabilities.network_isolation
                and capabilities.process_isolation
            )
            self.status = SandboxStatus(
                ready,
                executable,
                None if ready else str(response.get("warning") or "sandbox is not ready"),
                "windows-native",
                capabilities,
                None if response.get("remediation") is None else str(response.get("remediation")),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.status = SandboxStatus(
                False,
                executable,
                f"Windows sandbox helper check failed: {exc}",
                "windows-native",
            )

    def prepare(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        policy: SandboxPolicy,
    ) -> LaunchSpec:
        if policy.preset is SandboxPreset.DANGER_FULL_ACCESS:
            return LaunchSpec(command, cwd, backend="none", sandboxed=False)
        if not self.status.available or self.status.executable is None:
            raise RuntimeError(self.status.warning or "Windows sandbox is unavailable")
        arguments = [
            str(self.status.executable),
            "run",
            "--workspace",
            str(self.workspace),
            "--cwd",
            str(cwd.resolve()),
            "--preset",
            policy.preset.value,
        ]
        for root in policy.writable_roots:
            arguments.extend(("--writable-root", str(root.resolve())))
        if policy.network_enabled:
            arguments.append("--network")
        arguments.extend(("--", *command))
        return LaunchSpec(tuple(arguments), cwd, backend="windows-native", sandboxed=True)

    @staticmethod
    def classifies_denial(returncode: int | None, stderr: str) -> bool:
        text = stderr.casefold()
        return returncode != 0 and (
            "windcode-sandbox:" in text or "access is denied" in text or "permission denied" in text
        )
