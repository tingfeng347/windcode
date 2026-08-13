from __future__ import annotations

import asyncio

import pytest

from windcode.domain.messages import Role
from windcode.domain.subagents import SubagentResult, SubagentStatus
from windcode.runtime.subagents.completion import SubagentCompletionSource


def _result(
    task_name: str = "scout",
    status: SubagentStatus = SubagentStatus.COMPLETED,
    summary: str = "found 3 files",
) -> SubagentResult:
    return SubagentResult(
        subagent_id="abc123",
        task_name=task_name,
        status=status,
        summary=summary,
    )


@pytest.mark.asyncio
async def test_drain_inbound_returns_empty_when_nothing_delivered() -> None:
    source = SubagentCompletionSource()
    assert await source.drain_inbound() == ()


@pytest.mark.asyncio
async def test_deliver_then_drain_returns_formatted_message() -> None:
    source = SubagentCompletionSource()
    source.track()
    await source.deliver(_result())
    messages = await source.drain_inbound()
    assert len(messages) == 1
    assert messages[0].role is Role.USER
    text = messages[0].content[0].text  # type: ignore[union-attr]
    assert "scout" in text
    assert "完成" in text
    assert "found 3 files" in text


@pytest.mark.asyncio
async def test_drain_or_close_returns_immediately_when_no_pending() -> None:
    source = SubagentCompletionSource()
    assert await source.drain_or_close_inbound() == ()


@pytest.mark.asyncio
async def test_drain_or_close_waits_for_pending_completion() -> None:
    source = SubagentCompletionSource()
    source.track()

    async def deliver_later() -> None:
        await asyncio.sleep(0.05)
        await source.deliver(_result())

    task = asyncio.create_task(deliver_later())
    messages = await asyncio.wait_for(source.drain_or_close_inbound(), timeout=2.0)
    await task
    assert len(messages) == 1
    assert "scout" in messages[0].content[0].text  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_drain_or_close_returns_already_delivered_without_waiting() -> None:
    source = SubagentCompletionSource()
    source.track()
    await source.deliver(_result())
    messages = await asyncio.wait_for(source.drain_or_close_inbound(), timeout=0.5)
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_failed_status_included_in_message() -> None:
    source = SubagentCompletionSource()
    source.track()
    await source.deliver(
        _result(
            status=SubagentStatus.FAILED,
            summary="",
        )
    )
    messages = await source.drain_inbound()
    text = messages[0].content[0].text  # type: ignore[union-attr]
    assert "失败" in text


@pytest.mark.asyncio
async def test_multiple_completions_drained_together() -> None:
    source = SubagentCompletionSource()
    source.track()
    source.track()
    await source.deliver(_result("alpha"))
    await source.deliver(_result("beta"))
    messages = await source.drain_inbound()
    assert len(messages) == 2
    assert "alpha" in messages[0].content[0].text  # type: ignore[union-attr]
    assert "beta" in messages[1].content[0].text  # type: ignore[union-attr]
