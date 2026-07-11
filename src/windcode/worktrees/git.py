from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from windcode.worktrees.models import GitErrorCategory, WorktreeError


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    arguments: tuple[str, ...]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str


class GitRunner:
    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
        timeout_seconds: float | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> GitCommandResult:
        if cancelled is not None and cancelled():
            raise WorktreeError(GitErrorCategory.CANCELLED, "Git operation was cancelled")
        resolved_cwd = _resolve(cwd)
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "never",
                "GIT_ASKPASS": "/bin/false",
                "SSH_ASKPASS": "/bin/false",
            }
        )
        process = await asyncio.create_subprocess_exec(
            "git",
            *arguments,
            cwd=resolved_cwd,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise WorktreeError(
                GitErrorCategory.TIMEOUT,
                f"Git command timed out after {timeout:g} seconds",
            ) from exc
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        result = GitCommandResult(
            arguments=tuple(arguments),
            cwd=resolved_cwd,
            returncode=process.returncode or 0,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
        )
        if check and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "Git command failed"
            lowered = message.lower()
            category = (
                GitErrorCategory.NOT_REPOSITORY
                if "not a git repository" in lowered
                else GitErrorCategory.COMMAND_FAILED
            )
            raise WorktreeError(category, message)
        if cancelled is not None and cancelled():
            raise WorktreeError(GitErrorCategory.CANCELLED, "Git operation was cancelled")
        return result
