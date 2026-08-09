from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from windcode.domain.events import ToolFinished, ToolStarted
from windcode.domain.messages import Message, Role, ToolResultBlock, message_to_dict
from windcode.domain.tools import ToolContext, ToolEffect
from windcode.policy import PolicyRequest
from windcode.runtime.control import RunControl
from windcode.runtime.event_bus import EventBus
from windcode.runtime.report import ToolExecutionRecord
from windcode.runtime.scheduler import ScheduledCall, ToolScheduler

EventFields = Callable[[], dict[str, Any]]
UserRequest = Callable[[object], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class ToolTurnOutcome:
    message: Message
    records: tuple[ToolExecutionRecord, ...]


class ToolTurnRunner:
    """Execute and journal one scheduled batch of tool calls."""

    def __init__(
        self,
        scheduler: ToolScheduler,
        control: RunControl,
        event_bus: EventBus,
        event_fields: EventFields,
        run_id: str,
        request_user: UserRequest,
    ) -> None:
        self._scheduler = scheduler
        self._control = control
        self._event_bus = event_bus
        self._event_fields = event_fields
        self._run_id = run_id
        self._request_user = request_user
        scheduler.before_execute = self._before_execute

    async def _before_execute(
        self,
        call: ScheduledCall,
        request: PolicyRequest,
    ) -> None:
        await self._event_bus.publish(
            ToolStarted(
                **self._event_fields(),
                call_id=call.call_id,
                tool_name=call.tool_name,
                arguments=dict(call.arguments),
            ),
            durable=True,
        )
        side_effect = bool(
            request.effects
            & {
                ToolEffect.WORKSPACE_WRITE,
                ToolEffect.PROCESS,
                ToolEffect.NETWORK,
                ToolEffect.OUTSIDE_WORKSPACE,
            }
        )
        self._event_bus.session_store.append(
            "tool_started",
            {
                "call_id": call.call_id,
                "tool_name": call.tool_name,
                "side_effect": side_effect,
            },
            durable=side_effect,
        )

    async def execute(
        self,
        calls: tuple[ScheduledCall, ...],
        workspace: Path,
        raw_arguments: Mapping[str, Mapping[str, Any]],
    ) -> ToolTurnOutcome:
        self._control.reserve_tool_calls(len(calls))
        context = ToolContext(
            workspace=workspace,
            run_id=self._run_id,
            cancelled=lambda: self._control.cancelled,
            request_user=self._request_user,
        )
        results = await self._scheduler.execute(calls, context)
        blocks: list[ToolResultBlock] = []
        records: list[ToolExecutionRecord] = []
        for call, scheduled_result in zip(calls, results, strict=True):
            result = scheduled_result.result
            self._event_bus.session_store.append(
                "tool_finished",
                {"call_id": call.call_id, "is_error": result.is_error},
                durable=True,
            )
            await self._event_bus.publish(
                ToolFinished(**self._event_fields(), call_id=call.call_id, result=result),
                durable=True,
            )
            blocks.append(
                ToolResultBlock(
                    call.call_id,
                    call.tool_name,
                    result.output,
                    is_error=result.is_error,
                    artifact_ref=result.artifact_ref,
                )
            )
            records.append(ToolExecutionRecord(call.tool_name, raw_arguments[call.call_id], result))

        message = Message(Role.TOOL, tuple(blocks))
        self._event_bus.session_store.append(
            "conversation_message",
            message_to_dict(message),
            durable=True,
        )
        return ToolTurnOutcome(message, tuple(records))
