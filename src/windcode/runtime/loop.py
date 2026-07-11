from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from windcode.context import TokenEstimator, compact_context, truncate_context
from windcode.domain.errors import WindcodeError
from windcode.domain.events import (
    ApprovalRequested,
    ApprovalResponse,
    ContextCompacted,
    ModelFallback,
    ModelRetrying,
    ModelStarted,
    ReasoningStatus,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunResult,
    RunStarted,
    TextDeltaEvent,
    ToolFinished,
    ToolStarted,
    UsageUpdated,
    UserInputRequested,
    UserResponse,
)
from windcode.domain.messages import (
    Message,
    Role,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    message_to_dict,
)
from windcode.domain.models import (
    ModelRequest,
    ModelUsage,
    ReasoningDelta,
    TextDelta,
    ToolCallDelta,
    Usage,
)
from windcode.domain.tools import ToolContext, ToolEffect
from windcode.policy import ApprovalChoice, PolicyDecision, PolicyRequest
from windcode.providers import ModelTarget
from windcode.runtime.control import BudgetExceeded, RunControl
from windcode.runtime.event_bus import EventBus
from windcode.runtime.report import ToolExecutionRecord, build_run_result
from windcode.runtime.retry import stream_with_retry
from windcode.runtime.scheduler import ScheduledCall, ToolScheduler
from windcode.sessions import ArtifactStore, SessionStatus


class AgentBlocked(RuntimeError):
    pass


def _add_usage(left: Usage, right: Usage) -> Usage:
    return Usage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cache_read_tokens=left.cache_read_tokens + right.cache_read_tokens,
        cache_write_tokens=left.cache_write_tokens + right.cache_write_tokens,
    )


class AgentLoop:
    def __init__(
        self,
        *,
        session_id: str,
        run_id: str,
        model_chain: tuple[ModelTarget, ...],
        scheduler: ToolScheduler,
        control: RunControl,
        event_bus: EventBus,
        system_prompt: str,
        max_output_tokens: int | None = None,
        token_estimator: TokenEstimator | None = None,
        artifact_store: ArtifactStore | None = None,
        preserve_recent_turns: int = 8,
        max_tool_result_chars: int = 20_000,
        close_event_bus: bool = True,
    ) -> None:
        if not model_chain:
            raise ValueError("model_chain cannot be empty")
        self.session_id = session_id
        self.run_id = run_id
        self.model_chain = model_chain
        self.scheduler = scheduler
        self.control = control
        self.event_bus = event_bus
        self.system_prompt = system_prompt
        self.max_output_tokens = max_output_tokens
        self.token_estimator = token_estimator
        self.artifact_store = artifact_store
        self.preserve_recent_turns = preserve_recent_turns
        self.max_tool_result_chars = max_tool_result_chars
        self.close_event_bus = close_event_bus
        self._turn = 0
        self.scheduler.approval_handler = self._approval_handler
        self.scheduler.before_execute = self._before_tool_execute

    def _common(self, turn: int | None = None) -> dict[str, Any]:
        return {
            "event_id": uuid4().hex,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "turn": self._turn if turn is None else turn,
        }

    async def _approval_handler(
        self,
        request: PolicyRequest,
        decision: PolicyDecision,
    ) -> ApprovalChoice:
        await self.event_bus.publish(
            ApprovalRequested(
                **self._common(),
                request_id=request.request_id,
                summary=request.summary,
                risk=decision.risk.value,
                choices=tuple(choice.value for choice in decision.choices),
            ),
            durable=True,
        )
        response = await self.control.wait_for_response(request.request_id)
        if not isinstance(response, ApprovalResponse):
            raise ValueError("approval request received a user-question response")
        try:
            return ApprovalChoice(response.decision)
        except ValueError:
            return ApprovalChoice.DENY

    async def _request_user(self, payload: object) -> object:
        request_id = uuid4().hex
        questions = cast(tuple[dict[str, Any], ...], payload)
        await self.event_bus.publish(
            UserInputRequested(**self._common(), request_id=request_id, questions=questions),
            durable=True,
        )
        response = await self.control.wait_for_response(request_id)
        if not isinstance(response, UserResponse):
            raise ValueError("user question received an approval response")
        return response.answers

    async def _before_tool_execute(
        self,
        call: ScheduledCall,
        request: PolicyRequest,
    ) -> None:
        await self.event_bus.publish(
            ToolStarted(
                **self._common(),
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
        self.event_bus.session_store.append(
            "tool_started",
            {
                "call_id": call.call_id,
                "tool_name": call.tool_name,
                "side_effect": side_effect,
            },
            durable=side_effect,
        )

    async def _on_retry(self, target: ModelTarget, attempt: int, error: WindcodeError) -> None:
        await self.event_bus.publish(
            ModelRetrying(
                **self._common(),
                model=target.model,
                attempt=attempt,
                reason=str(error),
            )
        )

    async def _on_fallback(
        self,
        source: ModelTarget,
        target: ModelTarget,
        error: WindcodeError,
    ) -> None:
        await self.event_bus.publish(
            ModelFallback(
                **self._common(),
                from_model=source.model,
                to_model=target.model,
                reason=str(error),
            ),
            durable=True,
        )
        await self.event_bus.publish(ModelStarted(**self._common(), model=target.model))

    async def _terminal_failure(
        self,
        message: str,
        category: str,
        *,
        usage: Usage | None = None,
    ) -> RunResult:
        result = RunResult(status="failed", final_text=message, usage=usage or Usage())
        await self.event_bus.publish(
            RunFailed(**self._common(), message=message, category=category),
            durable=True,
        )
        self.event_bus.session_store.set_status(SessionStatus.FAILED)
        return result

    async def run(
        self,
        prompt: str,
        workspace: Path,
        initial_messages: tuple[Message, ...] = (),
    ) -> RunResult:
        user_message = Message(Role.USER, (TextBlock(prompt),))
        messages = (*initial_messages, user_message)
        self.event_bus.session_store.append(
            "conversation_message",
            message_to_dict(user_message),
            durable=True,
        )
        records: list[ToolExecutionRecord] = []
        total_usage = Usage()
        final_text = ""
        await self.event_bus.publish(RunStarted(**self._common(0), prompt=prompt), durable=True)
        try:
            while True:
                self._turn = self.control.start_model_step()
                primary = self.model_chain[0]
                await self.event_bus.publish(ModelStarted(**self._common(), model=primary.model))
                request = ModelRequest(
                    model=primary.model,
                    messages=messages,
                    system_prompt=self.system_prompt,
                    tools=self.scheduler.registry.schemas(),
                    max_output_tokens=self.max_output_tokens,
                )
                if self.token_estimator is not None:
                    before = self.token_estimator.estimate(request)
                    if before.should_compact or self.control.consume_compaction_request():
                        candidate = messages
                        if self.artifact_store is not None:
                            candidate = truncate_context(
                                messages,
                                self.artifact_store,
                                max_tool_result_chars=self.max_tool_result_chars,
                                preserve_recent_turns=self.preserve_recent_turns,
                            ).messages
                        compacted = await compact_context(
                            candidate,
                            primary.transport,
                            model=primary.model,
                            system_prompt=self.system_prompt,
                            preserve_recent_turns=self.preserve_recent_turns,
                        )
                        if compacted.compacted:
                            messages = compacted.messages
                            request = ModelRequest(
                                model=primary.model,
                                messages=messages,
                                system_prompt=self.system_prompt,
                                tools=self.scheduler.registry.schemas(),
                                max_output_tokens=self.max_output_tokens,
                            )
                            after = self.token_estimator.estimate(request)
                            await self.event_bus.publish(
                                ContextCompacted(
                                    **self._common(),
                                    before_tokens=before.estimated_tokens,
                                    after_tokens=after.estimated_tokens,
                                ),
                                durable=True,
                            )
                text_parts: list[str] = []
                call_order: list[str] = []
                calls: dict[str, dict[str, str]] = {}
                last_call_id = ""
                step_usage = Usage()
                async for _target, event in stream_with_retry(
                    self.model_chain,
                    request,
                    on_retry=self._on_retry,
                    on_fallback=self._on_fallback,
                ):
                    self.control.check()
                    if isinstance(event, TextDelta):
                        text_parts.append(event.text)
                        await self.event_bus.publish(
                            TextDeltaEvent(**self._common(), text=event.text)
                        )
                    elif isinstance(event, ReasoningDelta):
                        await self.event_bus.publish(
                            ReasoningStatus(**self._common(), status=event.summary)
                        )
                    elif isinstance(event, ToolCallDelta):
                        call_id = event.call_id or last_call_id
                        if not call_id:
                            call_id = uuid4().hex
                        if call_id not in calls:
                            calls[call_id] = {"name": event.name, "arguments": ""}
                            call_order.append(call_id)
                        calls[call_id]["name"] = event.name or calls[call_id]["name"]
                        calls[call_id]["arguments"] += event.arguments_delta
                        last_call_id = call_id
                    elif isinstance(event, ModelUsage):
                        step_usage = event.usage
                        await self.event_bus.publish(
                            UsageUpdated(
                                **self._common(), usage=_add_usage(total_usage, step_usage)
                            )
                        )
                    else:
                        step_usage = event.usage

                total_usage = _add_usage(total_usage, step_usage)
                text = "".join(text_parts)
                assistant_content: list[TextBlock | ToolCallBlock] = []
                if text:
                    assistant_content.append(TextBlock(text))
                    final_text = text

                scheduled: list[ScheduledCall] = []
                raw_arguments: dict[str, dict[str, Any]] = {}
                for call_id in call_order:
                    state = calls[call_id]
                    try:
                        decoded = json.loads(state["arguments"] or "{}")
                        if not isinstance(decoded, Mapping):
                            raise ValueError("tool arguments must be an object")
                        mapping = cast(Mapping[object, object], decoded)
                        arguments = {str(key): value for key, value in mapping.items()}
                    except (json.JSONDecodeError, ValueError) as exc:
                        arguments = {"_invalid_json": state["arguments"], "_error": str(exc)}
                    raw_arguments[call_id] = arguments
                    assistant_content.append(ToolCallBlock(call_id, state["name"], arguments))
                    scheduled.append(ScheduledCall(call_id, state["name"], arguments))
                assistant_message = Message(Role.ASSISTANT, tuple(assistant_content))
                messages = (*messages, assistant_message)
                self.event_bus.session_store.append(
                    "conversation_message",
                    message_to_dict(assistant_message),
                    durable=True,
                )

                if not scheduled:
                    result = build_run_result(final_text, tuple(records), usage=total_usage)
                    await self.event_bus.publish(
                        RunCompleted(**self._common(), result=result), durable=True
                    )
                    self.event_bus.session_store.set_status(SessionStatus.COMPLETED)
                    return result

                self.control.reserve_tool_calls(len(scheduled))
                context = ToolContext(
                    workspace=workspace,
                    run_id=self.run_id,
                    cancelled=lambda: self.control.cancelled,
                    request_user=self._request_user,
                )
                results = await self.scheduler.execute(tuple(scheduled), context)
                tool_blocks: list[ToolResultBlock] = []
                for call, scheduled_result in zip(scheduled, results, strict=True):
                    result = scheduled_result.result
                    self.event_bus.session_store.append(
                        "tool_finished",
                        {
                            "call_id": call.call_id,
                            "is_error": result.is_error,
                        },
                        durable=True,
                    )
                    await self.event_bus.publish(
                        ToolFinished(**self._common(), call_id=call.call_id, result=result),
                        durable=True,
                    )
                    tool_blocks.append(
                        ToolResultBlock(
                            call.call_id,
                            call.tool_name,
                            result.output,
                            is_error=result.is_error,
                            artifact_ref=result.artifact_ref,
                        )
                    )
                    records.append(
                        ToolExecutionRecord(call.tool_name, raw_arguments[call.call_id], result)
                    )
                tool_message = Message(Role.TOOL, tuple(tool_blocks))
                messages = (*messages, tool_message)
                self.event_bus.session_store.append(
                    "conversation_message",
                    message_to_dict(tool_message),
                    durable=True,
                )
        except asyncio.CancelledError:
            self.control.cancel()
            await self.event_bus.publish(RunCancelled(**self._common()), durable=True)
            self.event_bus.session_store.set_status(SessionStatus.CANCELLED)
            return RunResult(status="cancelled", final_text=final_text, usage=total_usage)
        except BudgetExceeded as exc:
            return await self._terminal_failure(str(exc), "budget", usage=total_usage)
        except AgentBlocked as exc:
            result = RunResult(status="blocked", final_text=str(exc), usage=total_usage)
            await self.event_bus.publish(
                RunFailed(**self._common(), message=str(exc), category="blocked"), durable=True
            )
            self.event_bus.session_store.set_status(SessionStatus.FAILED)
            return result
        except WindcodeError as exc:
            return await self._terminal_failure(str(exc), exc.category.value, usage=total_usage)
        except Exception as exc:
            return await self._terminal_failure(str(exc), "internal", usage=total_usage)
        finally:
            if self.close_event_bus:
                await self.event_bus.close()
