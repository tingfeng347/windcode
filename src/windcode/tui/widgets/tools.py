from __future__ import annotations

from textual.widgets import Static

from windcode.domain.events import ToolFinished, ToolProgress, ToolStarted

TOOL_LABELS = {
    "read_file": "读取文件",
    "write_file": "写入文件",
    "edit_file": "编辑文件",
    "apply_patch": "应用补丁",
    "glob": "查找文件",
    "grep": "搜索文本",
    "shell": "执行命令",
    "ask_user": "询问用户",
}


class ToolBlock(Static, can_focus=True):
    def __init__(self, event: ToolStarted) -> None:
        self.call_id = event.call_id
        self.tool_name = event.tool_name
        self.title = TOOL_LABELS.get(event.tool_name, event.tool_name)
        super().__init__(f"  ● {self.title} ...", classes="tool-block tool-block-loading")

    def progress(self, event: ToolProgress) -> None:
        self.update(f"  ● {self.title} ... {event.message}")

    def finish(self, event: ToolFinished) -> None:
        self.remove_class("tool-block-loading")
        marker = "✗" if event.result.is_error else "✓"
        if event.result.is_error:
            self.add_class("tool-block-error")
        exit_code = event.result.data.get("exit_code")
        detail = f" · 退出码 {exit_code}" if exit_code is not None else ""
        self.title = (
            f"{TOOL_LABELS.get(self.tool_name, self.tool_name)}{detail}"
            f" ({event.result.elapsed_seconds:.1f} 秒)"
        )
        self.update(f"  {marker} {self.title}")
