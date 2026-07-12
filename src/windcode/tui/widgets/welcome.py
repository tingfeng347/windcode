from __future__ import annotations

from pathlib import Path

from rich.text import Text as RichText
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Resize
from textual.timer import Timer
from textual.widgets import Static

WIDE_LOGO = r"""
             _           _
__      _____(_)_ __   __| | ___ ___   ___  ___
\ \ /\ / / _ \ | '_ \ / _` |/ __/ _ \ / _ \/ _ \
 \ V  V /  __/ | | | | (_| | (_| (_) |  __/  __/
  \_/\_/ \___|_|_| |_|\__,_|\___\___/ \___|\___|
""".strip("\n")

COMPACT_LOGO = "[ windcode ]"
MCP_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
LOGO_PALETTE = ("#59c7d6", "#5fa8e8", "#8b8fe8", "#d9a557", "#63c28d")


class WelcomeView(Vertical):
    """Brand-focused empty state for a new Windcode session."""

    def __init__(
        self,
        *,
        model: str,
        permission: str,
        sandbox: bool,
        workspace: Path,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._model = model
        self._permission = permission
        self._sandbox = sandbox
        self._workspace = workspace
        self._mcp_spinner_timer: Timer | None = None
        self._mcp_spinner_index = 0
        self._logo_timer: Timer | None = None
        self._logo_frame = 0

    def compose(self) -> ComposeResult:
        yield Static(self._logo(), id="welcome-logo")
        yield Static("本地编码 Agent", id="welcome-subtitle")
        yield Static(self._context_content(), id="welcome-context")
        yield Static("", id="welcome-notice")

    def on_mount(self) -> None:
        self._logo_timer = self.set_interval(0.12, self._tick_logo)

    def on_unmount(self) -> None:
        if self._logo_timer is not None:
            self._logo_timer.stop()
            self._logo_timer = None
        self.stop_mcp_loading()

    def _tick_logo(self) -> None:
        self._logo_frame = (self._logo_frame + 1) % 10_000
        self.query_one("#welcome-logo", Static).update(self._logo())

    def set_context(
        self,
        *,
        model: str,
        permission: str,
        sandbox: bool,
        workspace: Path,
    ) -> None:
        self._model = model
        self._permission = permission
        self._sandbox = sandbox
        self._workspace = workspace
        if self.is_mounted:
            self.query_one("#welcome-logo", Static).update(self._logo())
            self.query_one("#welcome-context", Static).update(self._context_content())

    def show_notice(self, text: str, *, error: bool = False) -> None:
        notice = self.query_one("#welcome-notice", Static)
        notice.set_class(error, "welcome-notice-error")
        notice.update(f"{'错误' if error else '状态'} · {text}")

    def clear_notice(self) -> None:
        if self.is_mounted:
            notice = self.query_one("#welcome-notice", Static)
            notice.remove_class("welcome-notice-error")
            notice.update("")

    def start_mcp_loading(self) -> None:
        self.stop_mcp_loading()
        self._mcp_spinner_index = 0
        self._render_mcp_loading()
        self._mcp_spinner_timer = self.set_interval(0.08, self._tick_mcp_loading)

    def stop_mcp_loading(self) -> None:
        if self._mcp_spinner_timer is not None:
            self._mcp_spinner_timer.stop()
            self._mcp_spinner_timer = None

    def _tick_mcp_loading(self) -> None:
        self._mcp_spinner_index = (self._mcp_spinner_index + 1) % len(MCP_SPINNER_FRAMES)
        self._render_mcp_loading()

    def _render_mcp_loading(self) -> None:
        if not self.is_mounted:
            return
        frame = MCP_SPINNER_FRAMES[self._mcp_spinner_index]
        notice = self.query_one("#welcome-notice", Static)
        notice.remove_class("welcome-notice-error")
        notice.update(f"{frame} 正在加载 MCP 服务...")

    def on_resize(self, event: Resize) -> None:
        del event
        self.query_one("#welcome-logo", Static).update(self._logo())

    def _logo(self) -> RichText:
        logo = COMPACT_LOGO if self.size.width and self.size.width < 64 else WIDE_LOGO
        output = RichText(justify="center")
        lines = logo.splitlines()
        for row, line in enumerate(lines):
            for column, char in enumerate(line):
                if char.isspace():
                    output.append(char)
                    continue
                wave = (column + row * 2 + self._logo_frame) // 3
                color = LOGO_PALETTE[wave % len(LOGO_PALETTE)]
                highlight = (column + row + self._logo_frame) % 17 in {0, 1}
                style = f"bold {color}" + (" on #26343d" if highlight else "")
                output.append(char, style=style)
            if row < len(lines) - 1:
                output.append("\n")
        return output

    def _context_content(self) -> RichText:
        permissions = {
            "plan": "计划",
            "default": "默认",
            "accept_edits": "自动编辑",
            "full_access": "完全授权",
        }
        context = RichText(justify="center")
        context.append(self._model, style="bold color(252)")
        context.append("  ·  ", style="color(240)")
        context.append(permissions.get(self._permission, self._permission), style="color(179)")
        context.append("  ·  ", style="color(240)")
        context.append(f"沙箱{'开启' if self._sandbox else '关闭'}", style="color(246)")
        context.append("\n")
        context.append(str(self._workspace), style="color(242)")
        return context
