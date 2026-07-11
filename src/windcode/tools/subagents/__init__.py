from windcode.runtime.subagents.coordinator import SubagentCoordinator
from windcode.tools.registry import ToolRegistry
from windcode.tools.subagents.cancel import CancelSubagentTool
from windcode.tools.subagents.integrate import IntegrateSubagentTool
from windcode.tools.subagents.list import ListSubagentsTool
from windcode.tools.subagents.spawn import SpawnSubagentsTool


def register_subagent_tools(
    registry: ToolRegistry,
    coordinator: SubagentCoordinator,
) -> None:
    for tool in (
        SpawnSubagentsTool(coordinator),
        ListSubagentsTool(coordinator),
        CancelSubagentTool(coordinator),
        IntegrateSubagentTool(coordinator),
    ):
        registry.register(tool)


__all__ = [
    "CancelSubagentTool",
    "IntegrateSubagentTool",
    "ListSubagentsTool",
    "SpawnSubagentsTool",
    "register_subagent_tools",
]
