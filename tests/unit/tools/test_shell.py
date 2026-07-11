import asyncio
from pathlib import Path

import pytest

from windcode.domain.tools import ToolContext
from windcode.tools.shell import ShellInput, ShellTool


@pytest.mark.asyncio
async def test_captures_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    result = await ShellTool().execute(
        ToolContext(tmp_path, "run", lambda: False),
        ShellInput(command="printf out; printf err >&2; exit 3"),
    )

    assert result.is_error
    assert result.data["exit_code"] == 3
    assert result.data["stdout"] == "out"
    assert result.data["stderr"] == "err"


@pytest.mark.asyncio
async def test_bounds_output(tmp_path: Path) -> None:
    result = await ShellTool(output_limit=16).execute(
        ToolContext(tmp_path, "run", lambda: False),
        ShellInput(command="printf 12345678901234567890"),
    )
    assert result.data["truncated"] is True
    assert "[output truncated]" in result.output


@pytest.mark.asyncio
async def test_timeout_reaps_process_group(tmp_path: Path) -> None:
    result = await ShellTool(default_timeout=0.05).execute(
        ToolContext(tmp_path, "run", lambda: False), ShellInput(command="sleep 10")
    )
    assert result.is_error
    assert result.data["timed_out"] is True


@pytest.mark.asyncio
async def test_cancel_reaps_process_group(tmp_path: Path) -> None:
    cancelled = False

    def is_cancelled() -> bool:
        return cancelled

    async def trigger() -> None:
        nonlocal cancelled
        await asyncio.sleep(0.05)
        cancelled = True

    trigger_task = asyncio.create_task(trigger())
    with pytest.raises(asyncio.CancelledError):
        await ShellTool().execute(
            ToolContext(tmp_path, "run", is_cancelled), ShellInput(command="sleep 10")
        )
    await trigger_task
