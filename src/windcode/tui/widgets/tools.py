from __future__ import annotations

from rich.text import Text as RichText
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
    "memory_search": "检索长期记忆",
    "memory_list": "列出长期记忆",
    "memory_get": "读取长期记忆",
    "memory_write": "写入长期记忆",
}


def format_duration(seconds: float) -> str:
    if seconds < 0.01:
        return "<0.01 秒"
    if seconds < 1:
        return f"{seconds:.2f} 秒"
    return f"{seconds:.1f} 秒"


class ToolBlock(Static, can_focus=True):
    def __init__(self, event: ToolStarted) -> None:
        self.call_id = event.call_id
        self.tool_name = event.tool_name
        command = event.arguments.get("command")
        self.command = str(command) if command is not None else None
        self.title = TOOL_LABELS.get(event.tool_name, event.tool_name)
        super().__init__(
            self._content(f"● {self.title} ..."),
            classes="tool-block tool-block-loading",
        )

    def _content(self, status: str) -> RichText:
        content = RichText(f"  {status}")
        if self.tool_name == "shell" and self.command:
            content.append("\n    ")
            content.append("bash:", style="bold cyan")
            content.append(f" {self.command}")
        return content

    def progress(self, event: ToolProgress) -> None:
        self.update(self._content(f"● {self.title} ... {event.message}"))

    def finish(self, event: ToolFinished) -> None:
        self.remove_class("tool-block-loading")
        marker = "✗" if event.result.is_error else "✓"
        if event.result.is_error:
            self.add_class("tool-block-error")
        exit_code = event.result.data.get("exit_code")
        detail = f" · 退出码 {exit_code}" if exit_code is not None else ""
        self.title = (
            f"{TOOL_LABELS.get(self.tool_name, self.tool_name)}{detail}"
            f" ({format_duration(event.result.elapsed_seconds)})"
        )
        self.update(self._content(f"{marker} {self.title}"))
