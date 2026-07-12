"""使用同一个 session_id 进行多轮有状态对话。"""
# ruff: noqa: RUF001

import asyncio
from pathlib import Path

from windcode import Windcode
from windcode.config import load_config
from windcode.types import RunRequest


async def run_turn(client: Windcode, workspace: Path, prompt: str, session_id: str | None) -> str:
    handle = client.start_run(RunRequest(prompt, workspace, session_id=session_id))
    current_session = session_id
    async for event in handle:
        current_session = event.session_id
        if event.kind == "text_delta":
            print(getattr(event, "text", ""), end="", flush=True)
    result = await handle.result()
    print(f"\n[状态: {result.status}]\n")
    if current_session is None:
        raise RuntimeError("运行没有返回 session_id")
    return current_session


async def main() -> None:
    workspace = Path.cwd().resolve()
    config = load_config(workspace)
    async with Windcode.open(config, workspace=workspace) as client:
        session_id = await run_turn(
            client,
            workspace,
            "请记住：这个示例项目的发布分支叫 release。只需简短确认。",
            None,
        )
        await run_turn(client, workspace, "我刚才说的发布分支叫什么？", session_id)
        print(f"可用于下次恢复的会话 ID: {session_id}")


if __name__ == "__main__":
    asyncio.run(main())
