from __future__ import annotations

from typing import TYPE_CHECKING

from windcode.sandbox import BubblewrapSandbox
from windcode.tools.apply_patch import ApplyPatchTool
from windcode.tools.ask_user import AskUserTool
from windcode.tools.edit_file import EditFileTool
from windcode.tools.glob import GlobTool
from windcode.tools.grep import GrepTool
from windcode.tools.read_file import ReadFileTool
from windcode.tools.registry import ToolRegistry
from windcode.tools.shell import ShellTool
from windcode.tools.write_file import WriteFileTool

if TYPE_CHECKING:
    from windcode.runtime.subagents.coordinator import SubagentCoordinator


def add_subagent_tools(registry: ToolRegistry, coordinator: SubagentCoordinator) -> ToolRegistry:
    from windcode.tools.subagents import register_subagent_tools

    register_subagent_tools(registry, coordinator)
    return registry


def create_builtin_registry(
    *,
    sandbox: BubblewrapSandbox | None = None,
    shell_timeout: float = 120.0,
) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ApplyPatchTool(),
        GlobTool(),
        GrepTool(),
        ShellTool(sandbox=sandbox, default_timeout=shell_timeout),
        AskUserTool(),
    ):
        registry.register(tool)
    return registry
