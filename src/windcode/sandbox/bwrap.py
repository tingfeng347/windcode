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


def detect_bubblewrap(executable: str = "bwrap", *, platform: str | None = None) -> SandboxStatus:
    capabilities = SandboxCapabilities(True, True, True)
    if platform == "nt":
        return SandboxStatus(
            False,
            None,
            "Windows system sandbox is unavailable; PowerShell commands require explicit approval",
            "bubblewrap",
            capabilities,
            "Approve each PowerShell command explicitly or select full_access mode.",
        )
    located = shutil.which(executable)
    if located is None:
        return SandboxStatus(
            False,
            None,
            "bubblewrap is unavailable; shell commands require elevated approval",
            "bubblewrap",
            capabilities,
            "Install bubblewrap (bwrap) or select danger_full_access explicitly.",
        )
    return SandboxStatus(
        True,
        Path(located).resolve(),
        backend="bubblewrap",
        capabilities=capabilities,
    )


class BubblewrapSandbox:
    def __init__(
        self,
        workspace: Path,
        status: SandboxStatus | None = None,
        *,
        read_only_workspace: bool = False,
        writable_paths: tuple[Path, ...] = (),
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.status = status or detect_bubblewrap()
        self.read_only_workspace = read_only_workspace
        self.writable_paths = tuple(path.expanduser().resolve() for path in writable_paths)

    def prepare(
        self,
        command: tuple[str, ...],
        *,
        cwd: object,
        policy: SandboxPolicy,
    ) -> LaunchSpec:
        if not isinstance(cwd, Path):
            raise TypeError("sandbox cwd must be a Path")
        if policy.preset is SandboxPreset.DANGER_FULL_ACCESS:
            return LaunchSpec(command, cwd, backend="none", sandboxed=False)
        wrapped = self.wrap(
            command,
            cwd=cwd,
            allow_network=policy.network_enabled,
            read_only_workspace=policy.preset is SandboxPreset.READ_ONLY,
            writable_paths=policy.writable_roots,
        )
        return LaunchSpec(wrapped, cwd, backend="bubblewrap", sandboxed=True)

    @staticmethod
    def classifies_denial(returncode: int | None, stderr: str) -> bool:
        return returncode != 0 and (
            "bwrap:" in stderr
            or "operation not permitted" in stderr.casefold()
            or "permission denied" in stderr.casefold()
        )

    def wrap(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        allow_network: bool = False,
        read_only_workspace: bool | None = None,
        writable_paths: tuple[Path, ...] | None = None,
    ) -> tuple[str, ...]:
        if not self.status.available or self.status.executable is None:
            raise RuntimeError(self.status.warning or "bubblewrap is unavailable")
        working_directory = (cwd or self.workspace).resolve()
        if not working_directory.is_relative_to(self.workspace):
            raise ValueError("sandbox working directory must be inside the workspace")
        temporary_mount = [] if self.workspace.is_relative_to(Path("/tmp")) else ["--tmpfs", "/tmp"]
        read_only = self.read_only_workspace if read_only_workspace is None else read_only_workspace
        writable = self.writable_paths if writable_paths is None else writable_paths
        workspace_mount = [] if read_only else ["--bind", str(self.workspace), str(self.workspace)]
        additional_mounts = [
            item
            for path in writable
            for item in ("--bind", str(path.resolve()), str(path.resolve()))
        ]
        arguments = [
            str(self.status.executable),
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/",
            "/",
            *workspace_mount,
            *additional_mounts,
            *temporary_mount,
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
