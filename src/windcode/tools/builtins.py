from __future__ import annotations

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
