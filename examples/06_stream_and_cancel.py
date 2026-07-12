"""消费完整事件流，并演示超时后取消运行。"""
# ruff: noqa: RUF001, RUF002

import asyncio
from pathlib import Path

from windcode import Windcode
from windcode.config import load_config
from windcode.sdk import RunHandle
from windcode.types import RunRequest


async def consume(handle: RunHandle) -> None:
    async for event in handle:
        if event.kind == "text_delta":
            print(getattr(event, "text", ""), end="", flush=True)
        elif event.kind in {"tool_started", "tool_finished"}:
            print(f"\n[{event.kind}] {getattr(event, 'tool_name', '')}")


async def main() -> None:
    workspace = Path.cwd().resolve()
    config = load_config(workspace)
    async with Windcode.open(config, workspace=workspace) as client:
        handle = client.start_run(
            RunRequest("检查项目结构并给出三个改进建议，不要修改文件。", workspace)
        )
        try:
            async with asyncio.timeout(30):
                await consume(handle)
        except TimeoutError:
            print("\n运行超过 30 秒，正在取消...")
            await handle.cancel()
        result = await handle.result()
        print(f"\n最终状态: {result.status}")


if __name__ == "__main__":
    asyncio.run(main())
