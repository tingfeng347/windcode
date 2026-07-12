"""注册一个带 Pydantic 参数校验的自定义只读工具。"""
# ruff: noqa: RUF001

import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from windcode import Windcode
from windcode.config import load_config
from windcode.types import RunRequest, ToolContext, ToolEffect, ToolResult


class ProjectStatsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suffix: str = Field(default=".py", description="需要统计的文件扩展名，例如 .py")


class ProjectStatsTool:
    name = "project_stats"
    description = "统计工作区内指定扩展名的文件数量和总行数。"
    input_model = ProjectStatsInput
    effects = frozenset({ToolEffect.READ})

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        parsed = ProjectStatsInput.model_validate(arguments)
        files = [path for path in context.workspace.rglob(f"*{parsed.suffix}") if path.is_file()]
        total_lines = 0
        for path in files:
            if context.cancelled():
                return ToolResult("统计已取消", is_error=True)
            try:
                total_lines += len(path.read_text(encoding="utf-8").splitlines())
            except UnicodeDecodeError:
                continue
        return ToolResult(
            f"{parsed.suffix} 文件 {len(files)} 个，共 {total_lines} 行",
            data={"file_count": len(files), "line_count": total_lines},
        )


async def main() -> None:
    workspace = Path.cwd().resolve()
    config = load_config(workspace)
    async with Windcode.open(config, workspace=workspace) as client:
        client.register_tool(ProjectStatsTool())
        handle = client.start_run(
            RunRequest("调用 project_stats 统计 Python 文件，并用一句中文总结。", workspace)
        )
        async for event in handle:
            if event.kind == "tool_started":
                print(f"调用工具: {getattr(event, 'tool_name', '')}")
            elif event.kind == "text_delta":
                print(getattr(event, "text", ""), end="", flush=True)
        result = await handle.result()
        print(f"\n状态: {result.status}")


if __name__ == "__main__":
    asyncio.run(main())
