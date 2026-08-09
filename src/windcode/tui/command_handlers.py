from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from windcode.application.contracts import CapabilityRecord, MemoryRecord, MemoryStatus
from windcode.sdk import Windcode


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    message: str | None = None
    open_panel: Literal["memory", "extensions"] | None = None
    extension_records: tuple[CapabilityRecord, ...] | None = None


class MemoryCommandHandler:
    """Own parsing, validation, and execution for memory subcommands."""

    def __init__(self, client: Windcode) -> None:
        self.client = client

    def _resolve(self, prefix: str) -> MemoryRecord:
        matches = tuple(
            item for item in self.client.list_memories() if item.memory_id.startswith(prefix)
        )
        if len(matches) != 1:
            raise ValueError("记忆 ID 不存在或前缀不唯一")
        return matches[0]

    def execute(self, arguments: tuple[str, ...], *, enabled: bool) -> CommandOutcome:
        if not arguments:
            return CommandOutcome(open_panel="memory")
        action = arguments[0]
        values = arguments[1:]
        if not enabled:
            if action == "status":
                return CommandOutcome("长期记忆: 已禁用")
            raise ValueError("长期记忆已在配置中禁用")
        if action == "status":
            records = self.client.list_memories()
            candidates = sum(item.status is MemoryStatus.CANDIDATE for item in records)
            active = sum(item.status is MemoryStatus.ACTIVE for item in records)
            return CommandOutcome(
                f"长期记忆: 已启用; 生效 {active}; 候选 {candidates}; 总计 {len(records)}"
            )
        if action == "candidates":
            records = self.client.list_memories(status=MemoryStatus.CANDIDATE)
            text = "\n".join(f"{item.memory_id[:10]}  {item.title}" for item in records)
            return CommandOutcome(text or "没有待确认的记忆候选")
        if action == "search":
            if not values:
                raise ValueError("用法: /memory search 关键词")
            records = self.client.search_memories(" ".join(values))
            text = "\n".join(
                f"{item.memory_id[:10]}  [{item.status.value}] {item.title}" for item in records
            )
            return CommandOutcome(text or "没有匹配的记忆")
        if action == "show":
            if len(values) != 1:
                raise ValueError("用法: /memory show ID")
            item = self._resolve(values[0])
            return CommandOutcome(
                f"{item.title}\n类型: {item.kind.value}; 范围: {item.scope.value}; "
                f"状态: {item.status.value}; 激活: {item.activation.value}; "
                f"优先级: {item.priority}\n摘要: {item.summary}\n\n{item.body}"
            )
        if action == "activation":
            if len(values) != 2:
                raise ValueError("用法: /memory activation ID <always|search|manual>")
            item = self._resolve(values[0])
            if item.status is not MemoryStatus.ACTIVE:
                raise ValueError("候选或非生效记忆必须先确认, 才能设置自动激活策略")
            updated = self.client.set_memory_activation(item.memory_id, values[1])
            return CommandOutcome(
                f"记忆激活策略已更新: {updated.title} -> {updated.activation.value}"
            )
        if action in {"confirm", "reject", "forget"}:
            if len(values) != 1:
                raise ValueError(f"用法: /memory {action} ID")
            item = self._resolve(values[0])
            if action == "confirm":
                updated = self.client.confirm_memory(item.memory_id)
                return CommandOutcome(f"记忆已确认: {updated.title}")
            if action == "reject":
                updated = self.client.reject_memory(item.memory_id)
                return CommandOutcome(f"记忆已拒绝: {updated.title}")
            self.client.delete_memory(item.memory_id)
            return CommandOutcome(f"记忆已删除: {item.title}")
        if action == "rebuild":
            count = self.client.rebuild_memory_index()
            return CommandOutcome(f"记忆索引已重建: {count} 条")
        raise ValueError(
            "用法: /memory [status|candidates|search|show|activation|confirm|reject|forget|rebuild]"
        )


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
