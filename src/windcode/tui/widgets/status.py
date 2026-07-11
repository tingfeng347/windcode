from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static


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
        permissions = {
            "plan": "计划",
            "default": "默认",
            "accept_edits": "自动编辑",
            "full_access": "完全授权",
        }
        states = {
            "idle": "空闲",
            "running": "运行中",
            "completed": "已完成",
            "unverified": "未验证",
            "failed": "失败",
            "cancelled": "已取消",
        }
        self.query_one("#mode-label", Static).update(
            f"  {states.get(state, state)} · {permissions.get(permission, permission)}"
        )
        delegation_label = {"explicit": "显式", "proactive": "主动"}.get(
            delegation or "", delegation or ""
        )
        suffix = f" · 委派: {delegation_label}" if delegation_label else ""
        self.query_one("#sandbox-label", Static).update(
            f"沙箱: {'开启' if sandbox else '关闭'}{suffix}"
        )
        self.query_one("#model-label", Static).update(model or "按配置")
