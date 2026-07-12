"""列出 MCP 扩展，并让模型搜索和调用 MCP 工具。"""
# ruff: noqa: RUF001, RUF002

import asyncio
from pathlib import Path

from windcode import Windcode
from windcode.config import load_config
from windcode.types import RunRequest


async def main() -> None:
    workspace = Path.cwd().resolve()
    config = load_config(workspace)
    if not config.extensions.mcp_servers:
        raise RuntimeError("请先在 .windcode/config.toml 的 extensions.mcp_servers 中配置 MCP")

    async with Windcode.open(config, workspace=workspace) as client:
        await client.wait_for_required_mcp()
        print("当前 MCP 能力:")
        for item in await client.list_extensions():
            if item.kind.value == "mcp_server":
                print(
                    f"- {item.public_name}: enabled={item.enabled}, trusted={item.trusted}, "
                    f"activation={item.activation.value}"
                )

        handle = client.start_run(
            RunRequest(
                "先调用 list_mcp_servers 查看状态，再按需使用 search_mcp_tools 搜索合适的工具，"
                "最后调用一个只读 MCP 工具并用中文总结结果。",
                workspace,
            )
        )
        async for event in handle:
            if event.kind == "tool_started":
                print(f"\n[MCP/工具] {getattr(event, 'tool_name', '')}")
            elif event.kind == "text_delta":
                print(getattr(event, "text", ""), end="", flush=True)
        result = await handle.result()
        print(f"\n状态: {result.status}")


if __name__ == "__main__":
    asyncio.run(main())
