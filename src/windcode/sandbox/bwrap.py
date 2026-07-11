from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SandboxStatus:
    available: bool
    executable: Path | None
    warning: str | None = None


def detect_bubblewrap(executable: str = "bwrap") -> SandboxStatus:
    located = shutil.which(executable)
    if located is None:
        return SandboxStatus(
            available=False,
            executable=None,
            warning="bubblewrap is unavailable; shell commands require elevated approval",
        )
    return SandboxStatus(available=True, executable=Path(located).resolve())


class BubblewrapSandbox:
    def __init__(self, workspace: Path, status: SandboxStatus | None = None) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.status = status or detect_bubblewrap()

    def wrap(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        allow_network: bool = False,
    ) -> tuple[str, ...]:
        if not self.status.available or self.status.executable is None:
            raise RuntimeError(self.status.warning or "bubblewrap is unavailable")
        working_directory = (cwd or self.workspace).resolve()
        if not working_directory.is_relative_to(self.workspace):
            raise ValueError("sandbox working directory must be inside the workspace")
        arguments = [
            str(self.status.executable),
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(self.workspace),
            str(self.workspace),
            "--tmpfs",
            "/tmp",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--chdir",
            str(working_directory),
        ]
        if not allow_network:
            arguments.append("--unshare-net")
        arguments.extend(("--", *command))
        return tuple(arguments)
