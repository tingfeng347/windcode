from __future__ import annotations

from pathlib import Path

from windcode.sdk import Windcode
from windcode.tui.command_handlers import CommandOutcome


class ExtensionCommandHandler:
    """Own parsing, confirmation, and execution for extension subcommands."""

    _MUTATIONS = frozenset({"install", "enable", "disable", "reload", "trust"})

    def __init__(self, client: Windcode, workspace: Path) -> None:
        self.client = client
        self.workspace = workspace
        self._pending_mutation: tuple[str, str | None] | None = None

    async def execute(self, arguments: tuple[str, ...], *, active: bool) -> CommandOutcome:
        action = arguments[0] if arguments else "list"
        target = arguments[1] if len(arguments) > 1 else None
        if len(arguments) > 2:
            raise ValueError("用法: /extensions [操作] [目标]")
        if active and action in self._MUTATIONS:
            raise ValueError("任务运行期间不能修改扩展状态")
        mutation = (action, target)
        if action == "list":
            if active:
                raise ValueError("任务运行期间不能管理扩展")
            return CommandOutcome(open_panel="extensions")
        if action in self._MUTATIONS and self._pending_mutation != mutation:
            self._pending_mutation = mutation
            target_label = "" if target is None else f" {target}"
            return CommandOutcome(f"确认扩展操作: {action}{target_label}; 再次输入相同命令执行")
        if action in self._MUTATIONS:
            self._pending_mutation = None
        message: str | None = None
        if action == "inspect":
            if target is None:
                raise ValueError("用法: /extensions inspect 目标")
            records = await self.client.inspect_extension(target)
        elif action == "install":
            if target is None:
                raise ValueError("用法: /extensions install 路径")
            result = await self.client.install_extension(
                Path(target).expanduser()  # noqa: ASYNC240 - local command parsing
            )
            message = (
                f"已安装 {result.manifest.plugin_id}, 默认禁用; 运行 /extensions reload 后刷新目录"
            )
            records = await self.client.list_extensions()
        elif action in {"enable", "disable"}:
            if target is None:
                raise ValueError(f"用法: /extensions {action} 目标")
            await self.client.set_extension_enabled(target, action == "enable")
            message = "扩展状态已更新; 显式 reload 后影响新运行"
            records = await self.client.list_extensions()
        elif action == "reload":
            await self.client.reload_extensions()
            records = await self.client.list_extensions()
        elif action == "trust":
            trust_path = (
                self.workspace if target is None else Path(target).expanduser()  # noqa: ASYNC240 - local command parsing
            )
            await self.client.trust_extension_workspace(trust_path)
            message = "工作区信任已记录; 显式 reload 后生效"
            records = await self.client.list_extensions()
        else:
            raise ValueError(f"未知扩展操作: {action}")
        return CommandOutcome(message, extension_records=records)
