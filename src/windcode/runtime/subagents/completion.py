from __future__ import annotations

import asyncio

from windcode.domain.messages import Message, Role, TextBlock
from windcode.domain.subagents import SubagentResult, SubagentStatus


def _format_completion(result: SubagentResult) -> Message:
    status_label = {
        SubagentStatus.COMPLETED: "完成",
        SubagentStatus.FAILED: "失败",
        SubagentStatus.CANCELLED: "已取消",
        SubagentStatus.BLOCKED: "已阻塞",
        SubagentStatus.CONFLICT: "集成冲突",
        SubagentStatus.INTEGRATION_FAILED: "集成验证失败",
        SubagentStatus.INTEGRATED: "已集成",
    }.get(result.status, result.status.value)

    parts = [
        f"[子智能体通知] {result.task_name} — {status_label}",
        f"摘要: {result.summary or '(无)'}",
    ]
    if result.commit:
        parts.append(f"提交: {result.commit}")
    if result.changed_files:
        parts.append(f"变更文件: {', '.join(result.changed_files)}")
    if result.error_message:
        parts.append(f"错误: {result.error_message}")

    return Message(
        Role.USER,
        (TextBlock("\n".join(parts)),),
        provider_metadata={
            "subagent_id": result.subagent_id,
            "task_name": result.task_name,
            "subagent_status": result.status.value,
        },
    )


class SubagentCompletionSource:
    """Inbound message source that delivers subagent completion notifications.

    The parent agent loop drains pending completions at the start of each turn
    (non-blocking). When the model has no more tool calls, ``drain_or_close``
    waits for the next running subagent before allowing the run to end.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Message] = asyncio.Queue()
        self._pending_count = 0

    def track(self) -> None:
        """Register a pending subagent whose completion has not been delivered."""
        self._pending_count += 1

    async def deliver(self, result: SubagentResult) -> None:
        """Push a subagent completion result into the inbound queue."""
        self._pending_count = max(0, self._pending_count - 1)
        await self._queue.put(_format_completion(result))

    async def drain_inbound(self) -> tuple[Message, ...]:
        """Non-blocking drain of already-completed results."""
        messages: list[Message] = []
        while not self._queue.empty():
            messages.append(self._queue.get_nowait())
        return tuple(messages)

    async def drain_or_close_inbound(self) -> tuple[Message, ...]:
        """Drain completed results; if subagents are still running, wait for
        the next completion (up to 5 minutes) before returning."""
        messages = list(await self.drain_inbound())
        if messages or self._pending_count <= 0:
            return tuple(messages)
        try:
            async with asyncio.timeout(300.0):
                messages.append(await self._queue.get())
        except TimeoutError:
            pass
        messages.extend(await self.drain_inbound())
        return tuple(messages)
