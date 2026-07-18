from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import cast

from windcode.domain.tools import ToolContext, ToolResult
from windcode.sandbox import LaunchSpec, SandboxBackend


def _kill_process_group(pid: int, signal_name: str) -> None:
    """Send a POSIX signal without exposing platform-specific attributes to type checking."""
    killpg = cast(Callable[[int, int], None] | None, getattr(os, "killpg", None))
    selected_signal = cast(int | None, getattr(signal, signal_name, None))
    if killpg is None or selected_signal is None:
        raise RuntimeError("process-group signaling is unavailable on this platform")
    killpg(pid, selected_signal)


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


async def terminate_process_tree(process: asyncio.subprocess.Process, *, windows: bool) -> None:
    if process.returncode is not None:
        return
    if windows:
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        except OSError:
            process.kill()
        await process.wait()
        return
    try:
        _kill_process_group(process.pid, "SIGTERM")
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=0.5)
    except TimeoutError:
        try:
            _kill_process_group(process.pid, "SIGKILL")
        except ProcessLookupError:
            pass
        await process.wait()


class ProcessRunner:
    def __init__(self, *, default_timeout: float = 120.0, output_limit: int = 1_000_000) -> None:
        self.default_timeout = default_timeout
        self.output_limit = output_limit

    async def run(
        self,
        spec: LaunchSpec,
        context: ToolContext,
        *,
        timeout_seconds: float | None = None,
        backend: SandboxBackend | None = None,
        original_command: str = "",
    ) -> ToolResult:
        started = monotonic()
        windows = os.name == "nt"
        kwargs: dict[str, object] = {
            "cwd": spec.cwd,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if spec.environment is not None:
            kwargs["env"] = spec.environment
        if windows:
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        else:
            kwargs["start_new_session"] = True
        process = await asyncio.create_subprocess_exec(*spec.command, **kwargs)  # type: ignore[arg-type]
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

        readers = (
            asyncio.create_task(read_stream(process.stdout, stdout, "stdout")),
            asyncio.create_task(read_stream(process.stderr, stderr, "stderr")),
        )
        selected_timeout = timeout_seconds or self.default_timeout
        error: str | None = None
        try:
            async with asyncio.timeout(selected_timeout):
                while process.returncode is None:
                    if context.cancelled():
                        raise asyncio.CancelledError
                    await asyncio.sleep(0.02)
                await asyncio.gather(*readers)
        except TimeoutError:
            error = "timeout"
            await terminate_process_tree(process, windows=windows)
            await asyncio.gather(*readers, return_exceptions=True)
        except asyncio.CancelledError:
            await terminate_process_tree(process, windows=windows)
            await asyncio.gather(*readers, return_exceptions=True)
            raise
        finally:
            for reader in readers:
                if not reader.done():
                    reader.cancel()

        stdout_text = stdout.data.decode("utf-8", errors="replace")
        stderr_text = stderr.data.decode("utf-8", errors="replace")
        output = stdout_text + (
            ("\n" if stdout_text and not stdout_text.endswith("\n") else "") + stderr_text
            if stderr_text
            else ""
        )
        if stdout.truncated or stderr.truncated:
            output += "\n[output truncated]"
        if error == "timeout":
            output = f"command timed out after {selected_timeout:g} seconds"
        sandbox_denial = bool(
            spec.sandboxed
            and backend is not None
            and backend.classifies_denial(process.returncode, stderr_text)
        )
        return ToolResult(
            output=output,
            is_error=error is not None or process.returncode != 0,
            elapsed_seconds=monotonic() - started,
            data={
                "error": error,
                "exit_code": process.returncode,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "truncated": stdout.truncated or stderr.truncated,
                "command": original_command,
                "sandbox_backend": spec.backend,
                "sandboxed": spec.sandboxed,
                "sandbox_denial": sandbox_denial,
                "timed_out": error == "timeout",
            },
        )
