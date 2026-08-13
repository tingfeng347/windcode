from __future__ import annotations

from dataclasses import dataclass
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

    async def _resolve(self, prefix: str) -> MemoryRecord:
        matches = tuple(
            item for item in await self.client.list_memories() if item.memory_id.startswith(prefix)
        )
        if len(matches) != 1:
            raise ValueError("记忆 ID 不存在或前缀不唯一")
        return matches[0]

    async def execute(self, arguments: tuple[str, ...], *, enabled: bool) -> CommandOutcome:
        if not arguments:
            return CommandOutcome(open_panel="memory")
        action = arguments[0]
        values = arguments[1:]
        if not enabled:
            if action == "status":
                return CommandOutcome("长期记忆: 已禁用")
            raise ValueError("长期记忆已在配置中禁用")
        if action == "status":
            records = await self.client.list_memories()
            candidates = sum(item.status is MemoryStatus.CANDIDATE for item in records)
            active = sum(item.status is MemoryStatus.ACTIVE for item in records)
            return CommandOutcome(
                f"长期记忆: 已启用; 生效 {active}; 候选 {candidates}; 总计 {len(records)}"
            )
        if action == "candidates":
            records = await self.client.list_memories(status=MemoryStatus.CANDIDATE)
            text = "\n".join(f"{item.memory_id[:10]}  {item.title}" for item in records)
            return CommandOutcome(text or "没有待确认的记忆候选")
        if action == "search":
            if not values:
                raise ValueError("用法: /memory search 关键词")
            records = await self.client.search_memories(" ".join(values))
            text = "\n".join(
                f"{item.memory_id[:10]}  [{item.status.value}] {item.title}" for item in records
            )
            return CommandOutcome(text or "没有匹配的记忆")
        if action == "show":
            if len(values) != 1:
                raise ValueError("用法: /memory show ID")
            item = await self._resolve(values[0])
            return CommandOutcome(
                f"{item.title}\n类型: {item.kind.value}; 范围: {item.scope.value}; "
                f"状态: {item.status.value}; 激活: {item.activation.value}; "
                f"优先级: {item.priority}\n摘要: {item.summary}\n\n{item.body}"
            )
        if action == "activation":
            if len(values) != 2:
                raise ValueError("用法: /memory activation ID <always|search|manual>")
            item = await self._resolve(values[0])
            if item.status is not MemoryStatus.ACTIVE:
                raise ValueError("候选或非生效记忆必须先确认, 才能设置自动激活策略")
            updated = await self.client.set_memory_activation(item.memory_id, values[1])
            return CommandOutcome(
                f"记忆激活策略已更新: {updated.title} -> {updated.activation.value}"
            )
        if action in {"confirm", "reject", "forget"}:
            if len(values) != 1:
                raise ValueError(f"用法: /memory {action} ID")
            item = await self._resolve(values[0])
            if action == "confirm":
                updated = await self.client.confirm_memory(item.memory_id)
                return CommandOutcome(f"记忆已确认: {updated.title}")
            if action == "reject":
                updated = await self.client.reject_memory(item.memory_id)
                return CommandOutcome(f"记忆已拒绝: {updated.title}")
            await self.client.delete_memory(item.memory_id)
            return CommandOutcome(f"记忆已删除: {item.title}")
        if action == "rebuild":
            count = await self.client.rebuild_memory_index()
            return CommandOutcome(f"记忆索引已重建: {count} 条")
        raise ValueError(
            "用法: /memory [status|candidates|search|show|activation|confirm|reject|forget|rebuild]"
        )
