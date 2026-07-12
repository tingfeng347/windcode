"""让父 Agent 并行委派两个只读研究任务。"""
# ruff: noqa: RUF001

import asyncio
from pathlib import Path

from windcode import Windcode
from windcode.config import DelegationMode, load_config
from windcode.types import RunRequest


async def main() -> None:
    workspace = Path.cwd().resolve()
    config = load_config(workspace)
    config = config.model_copy(
        update={"subagents": config.subagents.model_copy(update={"mode": DelegationMode.PROACTIVE})}
    )
    prompt = """
请使用 spawn_subagents 并行启动两个 researcher/read 子智能体：
1. 分析 src/windcode/runtime 的职责；
2. 分析 src/windcode/extensions 的职责。
等待两者完成后，汇总为一份不超过 8 条的中文架构说明。只读，不修改文件。
""".strip()

    async with Windcode.open(config, workspace=workspace) as client:
        handle = client.start_run(RunRequest(prompt, workspace, permission_mode="default"))
        async for event in handle:
            if event.kind.startswith("subagent_"):
                name = getattr(event, "task_name", "")
                summary = getattr(event, "summary", "")
                print(f"[{event.kind}] {name} {summary}")
            elif event.kind == "text_delta":
                print(getattr(event, "text", ""), end="", flush=True)
        result = await handle.result()
        print(f"\n状态: {result.status}; 子智能体数量: {len(handle.subagents())}")


if __name__ == "__main__":
    asyncio.run(main())
