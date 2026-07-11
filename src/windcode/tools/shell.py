from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from time import monotonic
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.sandbox import BubblewrapSandbox
from windcode.tools.filesystem import require_workspace_path


class ShellInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    cwd: str = "."
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600)
    network: bool = False


@dataclass(slots=True)
class _BoundedOutput:
    limit: int
    data: bytearray
    truncated: bool = False

    def append(self, chunk: bytes) -> None:
        remaining = self.limit - len(self.data)
        if remaining > 0:
            self.data.extend(chunk[:remaining])
        if len(chunk) > remaining:
            self.truncated = True


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=0.5)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


class ShellTool:
    name = "shell"
    description = "Run a bounded shell command with timeout, cancellation, and optional sandboxing."
    input_model = ShellInput
    effects = frozenset({ToolEffect.PROCESS})

    def __init__(
        self,
        *,
        sandbox: BubblewrapSandbox | None = None,
        default_timeout: float = 120.0,
        output_limit: int = 1_000_000,
    ) -> None:
        self.sandbox = sandbox
        self.default_timeout = default_timeout
        self.output_limit = output_limit

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        parsed = cast(ShellInput, arguments)
        cwd = require_workspace_path(context.workspace, parsed.cwd)
        command = ("bash", "-lc", parsed.command)
        if self.sandbox is not None:
            if not self.sandbox.status.available:
                return ToolResult(
                    output=self.sandbox.status.warning or "sandbox unavailable",
                    is_error=True,
                    data={"error": "sandbox_unavailable"},
                )
            command = self.sandbox.wrap(command, cwd=cwd, allow_network=parsed.network)

        started = monotonic()
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout = _BoundedOutput(self.output_limit, bytearray())
        stderr = _BoundedOutput(self.output_limit, bytearray())

        async def read_stream(
            stream: asyncio.StreamReader | None, target: _BoundedOutput, label: str
        ) -> None:
            if stream is None:
                return
            while chunk := await stream.read(4096):
                target.append(chunk)
                if context.progress is not None:
                    await context.progress(f"{label}: {chunk.decode('utf-8', errors='replace')}")

        readers = [
            asyncio.create_task(read_stream(process.stdout, stdout, "stdout")),
            asyncio.create_task(read_stream(process.stderr, stderr, "stderr")),
        ]
        timeout = parsed.timeout_seconds or self.default_timeout
        try:
            async with asyncio.timeout(timeout):
                while process.returncode is None:
                    if context.cancelled():
                        raise asyncio.CancelledError
                    await asyncio.sleep(0.02)
                await asyncio.gather(*readers)
        except TimeoutError:
            await _terminate_process_group(process)
            await asyncio.gather(*readers)
            elapsed = monotonic() - started
            return ToolResult(
                output=f"command timed out after {timeout:g} seconds",
                is_error=True,
                elapsed_seconds=elapsed,
                data={"error": "timeout", "exit_code": process.returncode, "timed_out": True},
            )
        except asyncio.CancelledError:
            await _terminate_process_group(process)
            await asyncio.gather(*readers, return_exceptions=True)
            raise
        finally:
            for reader in readers:
                if not reader.done():
                    reader.cancel()

        elapsed = monotonic() - started
        stdout_text = stdout.data.decode("utf-8", errors="replace")
        stderr_text = stderr.data.decode("utf-8", errors="replace")
        output = stdout_text
        if stderr_text:
            output += ("\n" if output and not output.endswith("\n") else "") + stderr_text
        truncated = stdout.truncated or stderr.truncated
        if truncated:
            output += "\n[output truncated]"
        return ToolResult(
            output=output,
            is_error=process.returncode != 0,
            elapsed_seconds=elapsed,
            data={
                "exit_code": process.returncode,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "truncated": truncated,
                "command": parsed.command,
            },
        )
