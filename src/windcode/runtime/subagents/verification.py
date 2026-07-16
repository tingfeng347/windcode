from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path

from windcode.domain.subagents import VerificationResult
from windcode.domain.tools import ToolContext
from windcode.sandbox import SandboxBackend, SandboxPolicy
from windcode.tools.shell import ShellInput, ShellTool


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


class VerificationRunner:
    def __init__(
        self,
        *,
        sandbox: SandboxBackend | None = None,
        sandbox_policy: SandboxPolicy | None = None,
        timeout_seconds: float = 120.0,
        output_limit: int = 20_000,
    ) -> None:
        self.shell = ShellTool(
            sandbox=sandbox,
            sandbox_policy=sandbox_policy,
            default_timeout=timeout_seconds,
            output_limit=output_limit,
        )
        self.output_limit = output_limit

    async def run(
        self,
        commands: Sequence[str],
        *,
        workspace: Path,
        run_id: str,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> tuple[VerificationResult, ...]:
        results: list[VerificationResult] = []
        context = ToolContext(_resolve(workspace), run_id, cancelled)
        for command in commands:
            if cancelled():
                raise asyncio.CancelledError
            tool_result = await self.shell.execute(context, ShellInput(command=command))
            exit_code_value = tool_result.data.get("exit_code")
            exit_code = exit_code_value if isinstance(exit_code_value, int) else None
            summary = tool_result.output[: self.output_limit]
            result = VerificationResult(command, exit_code, summary, not tool_result.is_error)
            results.append(result)
            if not result.passed:
                break
        return tuple(results)
