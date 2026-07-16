from __future__ import annotations

import shutil
from pathlib import Path

from windcode.sandbox.models import (
    LaunchSpec,
    SandboxCapabilities,
    SandboxPolicy,
    SandboxPreset,
    SandboxStatus,
)


def _quote(value: Path) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


class SeatbeltSandbox:
    def __init__(self, workspace: Path, executable: str = "sandbox-exec") -> None:
        self.workspace = workspace.expanduser().resolve()
        located = shutil.which(executable)
        capabilities = SandboxCapabilities(True, True, True)
        self.status = SandboxStatus(
            bool(located),
            None if located is None else Path(located).resolve(),
            warning=None if located else "macOS sandbox-exec is unavailable",
            backend="seatbelt",
            capabilities=capabilities,
            remediation=None
            if located
            else "Use a supported macOS release or approve one unsandboxed run.",
        )

    def prepare(
        self, command: tuple[str, ...], *, cwd: object, policy: SandboxPolicy
    ) -> LaunchSpec:
        if not isinstance(cwd, Path):
            raise TypeError("sandbox cwd must be a Path")
        if policy.preset is SandboxPreset.DANGER_FULL_ACCESS:
            return LaunchSpec(command, cwd, backend="none", sandboxed=False)
        if not self.status.available or self.status.executable is None:
            raise RuntimeError(self.status.warning or "seatbelt unavailable")
        writes = [] if policy.preset is SandboxPreset.READ_ONLY else [self.workspace]
        writes.extend(path.expanduser().resolve() for path in policy.writable_roots)
        clauses = ["(version 1)", "(deny default)", "(allow process*)", "(allow file-read*)"]
        clauses.extend(f'(allow file-write* (subpath "{_quote(path)}"))' for path in writes)
        clauses.append('(allow file-write* (subpath "/private/tmp"))')
        clauses.append("(allow network*)" if policy.network_enabled else "(deny network*)")
        profile = " ".join(clauses)
        wrapped = (str(self.status.executable), "-p", profile, "--", *command)
        return LaunchSpec(wrapped, cwd, backend="seatbelt", sandboxed=True)

    @staticmethod
    def classifies_denial(returncode: int | None, stderr: str) -> bool:
        text = stderr.casefold()
        return returncode != 0 and ("sandbox:" in text or "operation not permitted" in text)
