from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from windcode.domain.errors import RequiredExtensionError, WindcodeError
from windcode.domain.events import (
    ApprovalRequested,
    ApprovalResponse,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunResult,
    RunStarted,
    ToolFinished,
    ToolStarted,
    UserInputRequested,
    UserResponse,
)
from windcode.domain.messages import (
    Message,
    Role,
    TextBlock,
    ToolResultBlock,
    message_to_dict,
)
from windcode.domain.models import Usage
from windcode.domain.tools import ToolContext, ToolEffect
from windcode.policy import (
    ApprovalChoice,
    PolicyDecision,
    PolicyRequest,
    summarize_policy_arguments,
)
from windcode.runtime.control import BudgetExceeded, RunControl
from windcode.runtime.event_bus import EventBus
from windcode.runtime.model_turn import (
    ContextWindow,
    ModelSession,
    ModelTurnRunner,
    RunObservers,
)
from windcode.runtime.report import ToolExecutionRecord, build_run_result
from windcode.runtime.scheduler import ScheduledCall, ToolScheduler
from windcode.sessions import SessionStatus


class AgentBlocked(RuntimeError):
    pass


class InboundMessageSource(Protocol):
    async def drain_inbound(self) -> tuple[Message, ...]: ...

    async def drain_or_close_inbound(self) -> tuple[Message, ...]: ...


@dataclass(frozen=True, slots=True)
class RunIdentity:
    session_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class ToolRuntime:
    scheduler: ToolScheduler
    control: RunControl


@dataclass(frozen=True, slots=True)
class RunJournal:
    event_bus: EventBus
    close_on_exit: bool = True


class AgentLoop:
    def __init__(
        self,
        *,
        identity: RunIdentity,
        model: ModelSession,
        tools: ToolRuntime,
        journal: RunJournal,
        context: ContextWindow | None = None,
        observers: RunObservers | None = None,
        inbound_message_source: InboundMessageSource | None = None,
    ) -> None:
        if not model.model_chain:
            raise ValueError("model_chain cannot be empty")
        self.identity = identity
        self.model = model
        self.tools = tools
        self.journal = journal
        context = context or ContextWindow()
        observers = observers or RunObservers()
        self.context = context
        self.observers = observers
        self.session_id = identity.session_id
        self.run_id = identity.run_id
        self.model_chain = model.model_chain
        self.scheduler = tools.scheduler
        self.control = tools.control
        self.event_bus = journal.event_bus
        self.max_output_tokens = model.max_output_tokens
        self.model_stream_idle_timeout_seconds = model.stream_idle_timeout_seconds
        self.token_estimator = context.token_estimator
        self.artifact_store = context.artifact_store
        self.preserve_recent_turns = context.preserve_recent_turns
        self.max_tool_result_chars = context.max_tool_result_chars
        self.close_event_bus = journal.close_on_exit
        self.sourced_context_provider = observers.sourced_context
        self.compact_observer = observers.compact
        self.completion_observer = observers.completion
        self.inbound_message_source = inbound_message_source
        self._turn = 0
        self._model_turn = ModelTurnRunner(
            model,
            self.control,
            self.event_bus,
            self._common,
            self.scheduler,
            context,
            observers,
        )
        self.scheduler.approval_handler = self._approval_handler
        self.scheduler.before_execute = self._before_tool_execute

    @property
    def system_prompt(self) -> str:
        return self.model.system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self.model.system_prompt = value

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
                tool_name=request.tool_name,
                arguments_summary=summarize_policy_arguments(request),
                command_actions=(
                    ()
                    if request.command_analysis is None
                    else tuple(
                        action.model_dump(mode="json")
                        for action in request.command_analysis.actions
                    )
                ),
                cwd=request.cwd,
                network=request.network,
                sandbox_backend=request.sandbox_backend,
                sandbox_preset=request.sandbox_preset,
                escalation_reason=request.escalation_reason,
                proposed_rule=(
                    None
                    if request.proposed_rule is None
                    else request.proposed_rule.model_dump(mode="json")
                ),
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

    def _settle_pending_tool_calls(self, pending: tuple[ScheduledCall, ...]) -> None:
        """Persist cancelled results for tool calls left unanswered on exit.

        The assistant tool_calls message is persisted before the tools run, so
        bailing out mid-execution (cancellation, budget, error) would otherwise
        leave a dangling tool call that providers reject on the next run.
        """

        if not pending:
            return
        tool_message = Message(
            Role.TOOL,
            tuple(
                ToolResultBlock(
                    call.call_id,
                    call.tool_name,
                    "Tool call was interrupted before it produced a result.",
                    is_error=True,
                )
                for call in pending
            ),
        )
        self.event_bus.session_store.append(
            "conversation_message",
            message_to_dict(tool_message),
            durable=True,
        )

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

    async def record_startup_failure(self, error: WindcodeError) -> None:
        await self.event_bus.publish(
            RunFailed(**self._common(0), message=str(error), category=error.category.value),
            durable=True,
        )
        self.event_bus.session_store.set_status(SessionStatus.FAILED)

    async def _observe_completion(self, result: RunResult) -> None:
        if self.completion_observer is None:
            return
        try:
            await self.completion_observer(result)
        except RequiredExtensionError:
            raise
        except Exception:
            # Learning is best-effort and must never change task success.
            pass

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
        pending_calls: tuple[ScheduledCall, ...] = ()
        await self.event_bus.publish(RunStarted(**self._common(0), prompt=prompt), durable=True)
        try:
            while True:
                if self.inbound_message_source is not None:
                    inbound = await self.inbound_message_source.drain_inbound()
                    if inbound:
                        messages = (*messages, *inbound)
                        for message in inbound:
                            self.event_bus.session_store.append(
                                "conversation_message",
                                message_to_dict(message),
                                durable=True,
                            )
                self._turn = self.control.start_model_step()
                turn = await self._model_turn.execute(messages, total_usage)
                messages = turn.messages
                total_usage = turn.total_usage
                if turn.text:
                    final_text = turn.text
                scheduled = turn.scheduled_calls
                raw_arguments = turn.raw_arguments
                assistant_message = turn.assistant_message
                messages = (*messages, assistant_message)
                self.event_bus.session_store.append(
                    "conversation_message",
                    message_to_dict(assistant_message),
                    durable=True,
                )

                pending_calls = scheduled

                if not scheduled:
                    if self.inbound_message_source is not None:
                        inbound = await self.inbound_message_source.drain_or_close_inbound()
                        if inbound:
                            messages = (*messages, *inbound)
                            for message in inbound:
                                self.event_bus.session_store.append(
                                    "conversation_message",
                                    message_to_dict(message),
                                    durable=True,
                                )
                            continue
                    result = build_run_result(final_text, tuple(records), usage=total_usage)
                    await self._observe_completion(result)
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
                results = await self.scheduler.execute(scheduled, context)
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
                pending_calls = ()
        except asyncio.CancelledError:
            self._settle_pending_tool_calls(pending_calls)
            self.control.cancel()
            await self.event_bus.publish(RunCancelled(**self._common()), durable=True)
            self.event_bus.session_store.set_status(SessionStatus.CANCELLED)
            return RunResult(status="cancelled", final_text=final_text, usage=total_usage)
        except BudgetExceeded as exc:
            self._settle_pending_tool_calls(pending_calls)
            return await self._terminal_failure(str(exc), "budget", usage=total_usage)
        except AgentBlocked as exc:
            self._settle_pending_tool_calls(pending_calls)
            result = RunResult(status="blocked", final_text=str(exc), usage=total_usage)
            await self.event_bus.publish(
                RunFailed(**self._common(), message=str(exc), category="blocked"), durable=True
            )
            self.event_bus.session_store.set_status(SessionStatus.FAILED)
            return result
        except WindcodeError as exc:
            self._settle_pending_tool_calls(pending_calls)
            return await self._terminal_failure(str(exc), exc.category.value, usage=total_usage)
        except Exception as exc:
            self._settle_pending_tool_calls(pending_calls)
            return await self._terminal_failure(str(exc), "internal", usage=total_usage)
        finally:
            if self.close_event_bus:
                await self.event_bus.close()
