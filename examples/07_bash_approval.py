"""处理 shell 等高风险工具发出的审批请求。"""
# ruff: noqa: RUF001

import asyncio
from pathlib import Path

from windcode import Windcode
from windcode.config import load_config
from windcode.types import ApprovalRequested, ApprovalResponse, RunRequest


async def main() -> None:
    workspace = Path.cwd().resolve()
    config = load_config(workspace)
    async with Windcode.open(config, workspace=workspace) as client:
        handle = client.start_run(
            RunRequest(
                "请使用 shell 执行 `git status --short`，然后用中文解释输出。",
                workspace,
                permission_mode="default",
            )
        )
        async for event in handle:
            if isinstance(event, ApprovalRequested):
                print(f"\n审批请求: {event.summary}")
                print(f"风险: {event.risk}; 可选项: {', '.join(event.choices)}")
                # 示例只允许本次请求。生产代码应结合 tool_name、参数和风险进行判断。
                decision = "allow_once" if "allow_once" in event.choices else "deny"
                await handle.respond(ApprovalResponse(event.request_id, decision))
            elif event.kind == "text_delta":
                print(getattr(event, "text", ""), end="", flush=True)
        result = await handle.result()
        print(f"\n状态: {result.status}")


if __name__ == "__main__":
    asyncio.run(main())
