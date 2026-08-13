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

    Both ``drain_inbound`` and ``drain_or_close_inbound`` are non-blocking — they
    return only results that have already been delivered. This allows the parent
    agent loop to finish its run (so the user can interact) while subagents keep
    running in the background. Completions are picked up at the start of the next
    run via ``drain_inbound``.
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
        """Non-blocking drain — never waits for running subagents.

        Returns already-completed results so the parent run can end and the user
        can continue interacting. Pending subagents survive to the next run.
        """
        return await self.drain_inbound()
