from __future__ import annotations

from rich.text import Text as RichText
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from windcode.tui.permission_display import permission_label, permission_style


class StatusBar(Horizontal):
    def compose(self) -> ComposeResult:
        yield Static("", id="mode-label")
        yield Static("", id="sandbox-label")
        yield Static("", id="model-label")

    def set_state(
        self,
        *,
        model: str | None,
        permission: str,
        sandbox: bool,
        state: str,
        delegation: str | None = None,
    ) -> None:
        states = {
            "idle": "空闲",
            "running": "运行中",
            "completed": "已完成",
            "unverified": "已完成 · 未验证",
            "failed": "失败",
            "cancelled": "已取消",
        }
        mode_content = RichText()
        mode_content.append(f"  {states.get(state, state)}", style="#5fa8e8")
        mode_content.append(" · ", style="#88939b")
        mode_content.append(permission_label(permission), style=permission_style(permission))
        self.query_one("#mode-label", Static).update(mode_content)
        delegation_label = {"explicit": "显式", "proactive": "主动"}.get(
            delegation or "", delegation or ""
        )
        suffix = f" · 委派: {delegation_label}" if delegation_label else ""
        self.query_one("#sandbox-label", Static).update(
            f"沙箱: {'开启' if sandbox else '关闭'}{suffix}"
        )
        self.query_one("#model-label", Static).update(model or "按配置")
