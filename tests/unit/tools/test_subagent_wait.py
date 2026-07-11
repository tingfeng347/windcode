from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from windcode.domain.subagents import SubagentResult, SubagentStatus
from windcode.domain.tools import ToolContext
from windcode.runtime.subagents.coordinator import SubagentCoordinator
from windcode.tools.subagents.wait import WaitSubagentsInput, WaitSubagentsTool


class WaitingCoordinator:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def wait(self, subagent_id: str) -> SubagentResult:
        await self.release.wait()
        return SubagentResult(
            subagent_id,
            "weather",
            SubagentStatus.COMPLETED,
            "done",
        )

    def list(self) -> tuple[object, ...]:
        return ()


async def test_wait_subagents_blocks_once_until_terminal_result(tmp_path: Path) -> None:
    coordinator = WaitingCoordinator()
    tool = WaitSubagentsTool(cast(SubagentCoordinator, coordinator))
    pending = asyncio.create_task(
        tool.execute(
            ToolContext(tmp_path, "run", lambda: False),
            WaitSubagentsInput(subagent_ids=("child",), timeout_seconds=1),
        )
    )
    await asyncio.sleep(0)
    assert not pending.done()

    coordinator.release.set()
    result = await pending
    assert not result.is_error
    assert result.data["subagents"][0]["status"] == "completed"
