from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from pathlib import Path
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from windcode.config import PermissionMode, SubagentConfig
from windcode.domain.events import (
    AgentEventType,
    ApprovalRequested,
    ReasoningStatus,
    SubagentBlocked,
    SubagentCancelled,
    SubagentCleanup,
    SubagentCompleted,
    SubagentConflict,
    SubagentEvent,
    SubagentFailed,
    SubagentIntegrated,
    SubagentProgress,
    SubagentQueued,
    SubagentStarted,
    ToolStarted,
    UsageUpdated,
)
from windcode.domain.models import Usage
from windcode.domain.subagents import (
    SubagentRecord,
    SubagentResult,
    SubagentStatus,
    SubagentTaskKind,
    SubagentTaskSpec,
    VerificationResult,
    sort_subagent_records,
    subagent_record_from_dict,
    subagent_record_to_dict,
    transition_subagent,
)
from windcode.runtime.event_bus import EventBus
from windcode.runtime.subagents.approvals import ApprovalRouter
from windcode.runtime.subagents.budgets import AggregateBudget
from windcode.runtime.subagents.factory import ChildRuntime, ChildRuntimeFactory
from windcode.runtime.subagents.verification import VerificationRunner
from windcode.worktrees import GitBaseline, WorktreeLease, WorktreeManager


class SubagentCoordinatorError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        self.category = category
        super().__init__(message)


class SubagentCoordinator:
    def __init__(
        self,
        *,
        parent_session_id: str,
        parent_run_id: str,
        workspace: Path,
        permission_mode: PermissionMode,
        config: SubagentConfig,
        event_bus: EventBus,
        factory: ChildRuntimeFactory,
        worktrees: WorktreeManager,
        verification: VerificationRunner,
        network_enabled: bool = False,
        event_observer: Callable[[SubagentEvent], Awaitable[None]] | None = None,
    ) -> None:
        self.parent_session_id = parent_session_id
        self.parent_run_id = parent_run_id
        self.workspace = workspace
        self.permission_mode = permission_mode
        self.config = config
        self.event_bus = event_bus
        self.factory = factory
        self.worktrees = worktrees
        self.verification = verification
        self.network_enabled = network_enabled
        self.event_observer = event_observer
        self.aggregate_budget = AggregateBudget(
            max_model_steps=config.max_total_model_steps,
            max_tool_calls=config.max_total_tool_calls,
            max_runtime_seconds=config.max_runtime_seconds,
        )
        self.approvals = ApprovalRouter(
            parent_session_id=parent_session_id,
            parent_run_id=parent_run_id,
            publish=self._publish_approval,
        )
        self._records: dict[str, SubagentRecord] = {}
        self._results: dict[str, SubagentResult] = {}
        self._leases: dict[str, WorktreeLease] = {}
        self._runtimes: dict[str, ChildRuntime] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._completion: dict[str, asyncio.Future[SubagentResult]] = {}
        self._usage: dict[str, Usage] = {}
        self._queue: deque[str] = deque()
        self._active = 0
        self._closed = False
        self._lock = asyncio.Lock()

    def set_permission_mode(self, mode: PermissionMode) -> None:
        previous = self.permission_mode
        self.permission_mode = mode
        for runtime in self._runtimes.values():
            policy = runtime.loop.scheduler.policy
            if runtime.record.spec.kind is SubagentTaskKind.READ and (
                not self.factory.config.sandbox.enabled or not policy.sandbox_available
            ):
                effective = PermissionMode.PLAN
            else:
                effective = mode
            policy.set_mode(effective)
            runtime.loop.system_prompt = runtime.loop.system_prompt.replace(
                f"权限模式: {previous.value}.",
                f"权限模式: {effective.value}.",
            )

    async def _publish_approval(self, event: ApprovalRequested) -> None:
        await self.event_bus.publish(event, durable=True)

    def list(self) -> tuple[SubagentRecord, ...]:
        return sort_subagent_records(tuple(self._records.values()))

    def result(self, subagent_id: str) -> SubagentResult | None:
        return self._results.get(subagent_id)

    async def wait(self, subagent_id: str) -> SubagentResult:
        try:
            future = self._completion[subagent_id]
        except KeyError as exc:
            raise SubagentCoordinatorError("unknown_subagent", subagent_id) from exc
        return await asyncio.shield(future)

    async def spawn(self, specs: tuple[SubagentTaskSpec, ...]) -> tuple[SubagentRecord, ...]:
        if not specs:
            raise SubagentCoordinatorError("invalid_task", "at least one subagent task is required")
        async with self._lock:
            if self._closed:
                raise SubagentCoordinatorError("closed", "subagent coordinator is closed")
            if len(self._records) + len(specs) > self.config.max_tasks:
                raise SubagentCoordinatorError(
                    "capacity_exceeded",
                    f"subagent task limit is {self.config.max_tasks}",
                )
            names = [spec.task_name for spec in specs]
            existing_names = {record.spec.task_name for record in self._records.values()}
            if len(set(names)) != len(names) or existing_names.intersection(names):
                raise SubagentCoordinatorError(
                    "duplicate_task_name", "task names must be unique within a parent run"
                )
            unavailable_network = [
                spec.task_name
                for spec in specs
                if spec.requires_network and not self.network_enabled
            ]
            if unavailable_network:
                raise SubagentCoordinatorError(
                    "capability_unavailable",
                    "external network is unavailable for subagent tasks: "
                    + ", ".join(unavailable_network),
                )
            baseline: GitBaseline | None = None
            if any(spec.kind is SubagentTaskKind.WRITE for spec in specs):
                try:
                    baseline = await self.worktrees.validate_parent(self.workspace)
                except Exception as exc:
                    raise SubagentCoordinatorError("write_workspace_blocked", str(exc)) from exc

            start_index = len(self._records)
            created: list[SubagentRecord] = []
            loop = asyncio.get_running_loop()
            for offset, spec in enumerate(specs):
                record = SubagentRecord(
                    subagent_id=uuid4().hex,
                    parent_session_id=self.parent_session_id,
                    parent_run_id=self.parent_run_id,
                    task_index=start_index + offset,
                    spec=spec,
                    base_commit=(
                        baseline.commit
                        if baseline is not None and spec.kind is SubagentTaskKind.WRITE
                        else None
                    ),
                )
                self._records[record.subagent_id] = record
                self._completion[record.subagent_id] = loop.create_future()
                self._queue.append(record.subagent_id)
                await self._persist(record)
                await self._publish_record_event(record, SubagentQueued)
                created.append(record)
            self._schedule_locked()
            return tuple(created)

    def _schedule_locked(self) -> None:
        while self._queue and self._active < self.config.max_concurrent:
            subagent_id = self._queue.popleft()
            if self._records[subagent_id].status is not SubagentStatus.QUEUED:
                continue
            self._active += 1
            task = asyncio.create_task(self._execute(subagent_id))
            self._tasks[subagent_id] = task

    async def _persist(self, record: SubagentRecord) -> None:
        self.event_bus.session_store.append(
            "subagent_record", subagent_record_to_dict(record), durable=True
        )

    def _event_common(self, record: SubagentRecord) -> dict[str, Any]:
        return {
            "event_id": uuid4().hex,
            "session_id": self.parent_session_id,
            "run_id": self.parent_run_id,
            "turn": 0,
            "parent_run_id": self.parent_run_id,
            "subagent_id": record.subagent_id,
            "task_index": record.task_index,
            "role": record.spec.role.value,
            "task_name": record.spec.task_name,
        }

    async def _publish_record_event(
        self,
        record: SubagentRecord,
        event_type: type[SubagentEvent],
        **values: Any,
    ) -> None:
        event = cast(AgentEventType, event_type(**self._event_common(record), **values))
        await self.event_bus.publish(event, durable=True)
        if self.event_observer is not None and isinstance(event, SubagentEvent):
            await self.event_observer(event)

    async def _replace_record(self, record: SubagentRecord) -> None:
        self._records[record.subagent_id] = record
        await self._persist(record)

    async def _transition(
        self,
        record: SubagentRecord,
        status: SubagentStatus,
        *,
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> SubagentRecord:
        updated = transition_subagent(
            record,
            status,
            error_category=error_category,
            error_message=error_message,
        )
        await self._replace_record(updated)
        return updated

    async def _forward_child_events(self, runtime: ChildRuntime) -> None:
        reasoning = ""
        last_reasoning_publish = monotonic()
        async for event in runtime.event_bus.subscribe():
            record = self._records[runtime.record.subagent_id]
            if isinstance(event, UsageUpdated):
                self._usage[record.subagent_id] = event.usage
                await self._publish_record_event(
                    record,
                    SubagentProgress,
                    summary="usage updated",
                    activity="model usage",
                    usage=event.usage,
                )
            elif isinstance(event, ToolStarted):
                await self._publish_record_event(
                    record,
                    SubagentProgress,
                    summary="tool started",
                    activity=event.tool_name,
                )
            elif isinstance(event, ReasoningStatus):
                reasoning += event.status
                if monotonic() - last_reasoning_publish >= 0.5:
                    await self._publish_record_event(
                        record,
                        SubagentProgress,
                        summary="reasoning",
                        activity=reasoning[-500:],
                    )
                    reasoning = ""
                    last_reasoning_publish = monotonic()
        if reasoning:
            record = self._records[runtime.record.subagent_id]
            await self._publish_record_event(
                record,
                SubagentProgress,
                summary="reasoning",
                activity=reasoning[-500:],
            )

    async def _execute(self, subagent_id: str) -> None:
        try:
            record = self._records[subagent_id]
            workspace = self.workspace
            if record.spec.kind is SubagentTaskKind.WRITE:
                if record.base_commit is None:
                    raise SubagentCoordinatorError("missing_baseline", "write task has no baseline")
                baseline = GitBaseline(
                    self.workspace,
                    "",
                    record.base_commit,
                )
                validated = await self.worktrees.validate_parent(self.workspace)
                baseline = replace(
                    baseline, repository=validated.repository, branch=validated.branch
                )
                lease = await self.worktrees.create(
                    record.subagent_id,
                    record.spec.task_name,
                    baseline,
                    parent_run_id=self.parent_run_id,
                )
                self._leases[subagent_id] = lease
                workspace = lease.path
                record = replace(record, branch=lease.branch, worktree_path=lease.path)
                await self._replace_record(record)

            runtime = self.factory.create(
                record,
                workspace=workspace,
                parent_permission=self.permission_mode,
                aggregate_budget=self.aggregate_budget,
                approval_router=self.approvals,
            )
            self._runtimes[subagent_id] = runtime
            record = await self._transition(runtime.record, SubagentStatus.RUNNING)
            await self._publish_record_event(
                record,
                SubagentStarted,
                summary="subagent started",
                workspace=str(workspace),
            )
            forwarding = asyncio.create_task(self._forward_child_events(runtime))
            try:
                run_result = await runtime.loop.run(runtime.prompt, runtime.workspace)
            finally:
                await forwarding

            if runtime.control.cancelled or run_result.status == "cancelled":
                await self._finish_cancelled(record, "child run cancelled", usage=run_result.usage)
                return
            if run_result.status == "blocked":
                blocked = await self._transition(
                    record,
                    SubagentStatus.BLOCKED,
                    error_category="clarification_required",
                    error_message=run_result.final_text,
                )
                result = SubagentResult(
                    subagent_id,
                    record.spec.task_name,
                    SubagentStatus.BLOCKED,
                    run_result.final_text,
                    usage=run_result.usage,
                    error_category="clarification_required",
                    error_message=run_result.final_text,
                )
                await self._complete(result)
                await self._publish_record_event(
                    blocked,
                    SubagentBlocked,
                    summary="clarification required",
                    reason=run_result.final_text,
                )
                return
            if run_result.status == "failed":
                await self._finish_failed(
                    record,
                    "child_run_failed",
                    run_result.final_text,
                    usage=run_result.usage,
                )
                return

            changed_files: tuple[str, ...] = ()
            commit: str | None = None
            if record.spec.kind is SubagentTaskKind.WRITE:
                inspected = await self.worktrees.inspect(self._leases[subagent_id])
                if not inspected.clean or inspected.commit is None:
                    await self._finish_failed(
                        record,
                        "invalid_write_delivery",
                        "write task must finish with a clean new commit",
                    )
                    return
                changed_files = inspected.changed_files
                commit = inspected.commit
                record = replace(record, commit=commit)
            record = await self._transition(record, SubagentStatus.COMPLETED)
            verification = tuple(
                VerificationResult(item, None, "reported by child", True)
                for item in run_result.verification
            )
            result = SubagentResult(
                subagent_id,
                record.spec.task_name,
                SubagentStatus.COMPLETED,
                run_result.final_text,
                changed_files,
                commit,
                verification,
                run_result.usage,
            )
            await self._complete(result)
            await self._publish_record_event(
                record,
                SubagentCompleted,
                summary=run_result.final_text[:500],
                commit=commit,
                changed_files=changed_files,
                verification=run_result.verification,
                usage=run_result.usage,
            )
        except asyncio.CancelledError:
            record = self._records[subagent_id]
            if record.status in {SubagentStatus.QUEUED, SubagentStatus.RUNNING}:
                await self._finish_cancelled(record, "subagent cancelled")
        except Exception as exc:
            record = self._records[subagent_id]
            if record.status in {SubagentStatus.QUEUED, SubagentStatus.RUNNING}:
                await self._finish_failed(record, type(exc).__name__, str(exc))
        finally:
            self._runtimes.pop(subagent_id, None)
            self.approvals.cancel(subagent_id)
            async with self._lock:
                self._active -= 1
                self._tasks.pop(subagent_id, None)
                self._schedule_locked()

    async def _complete(self, result: SubagentResult) -> None:
        self._results[result.subagent_id] = result
        await self._persist_result(result)
        future = self._completion[result.subagent_id]
        if not future.done():
            future.set_result(result)

    async def _persist_result(self, result: SubagentResult) -> None:
        self.event_bus.session_store.append(
            "subagent_result",
            {
                "subagent_id": result.subagent_id,
                "task_name": result.task_name,
                "status": result.status.value,
                "summary": result.summary,
                "commit": result.commit,
                "error_category": result.error_category,
                "error_message": result.error_message,
                "usage": {
                    "input_tokens": result.usage.input_tokens,
                    "output_tokens": result.usage.output_tokens,
                    "cache_read_tokens": result.usage.cache_read_tokens,
                    "cache_write_tokens": result.usage.cache_write_tokens,
                },
            },
            durable=True,
        )

    async def _finish_failed(
        self,
        record: SubagentRecord,
        category: str,
        message: str,
        *,
        usage: Usage | None = None,
    ) -> None:
        final_usage = usage or self._usage.get(record.subagent_id, Usage())
        failed = await self._transition(
            record,
            SubagentStatus.FAILED,
            error_category=category,
            error_message=message,
        )
        result = SubagentResult(
            record.subagent_id,
            record.spec.task_name,
            SubagentStatus.FAILED,
            message,
            usage=final_usage,
            error_category=category,
            error_message=message,
        )
        await self._complete(result)
        await self._publish_record_event(
            failed,
            SubagentFailed,
            summary="subagent failed",
            message=message,
            category=category,
            usage=final_usage,
        )

    async def _finish_cancelled(
        self,
        record: SubagentRecord,
        reason: str,
        *,
        usage: Usage | None = None,
    ) -> None:
        final_usage = usage or self._usage.get(record.subagent_id, Usage())
        cancelled = await self._transition(
            record,
            SubagentStatus.CANCELLED,
            error_category="cancelled",
            error_message=reason,
        )
        result = SubagentResult(
            record.subagent_id,
            record.spec.task_name,
            SubagentStatus.CANCELLED,
            reason,
            usage=final_usage,
            error_category="cancelled",
            error_message=reason,
        )
        await self._complete(result)
        await self._publish_record_event(
            cancelled,
            SubagentCancelled,
            summary="subagent cancelled",
            reason=reason,
            usage=final_usage,
        )

    async def cancel(self, subagent_id: str) -> SubagentRecord:
        try:
            record = self._records[subagent_id]
        except KeyError as exc:
            raise SubagentCoordinatorError("unknown_subagent", subagent_id) from exc
        if record.status is SubagentStatus.QUEUED:
            await self._finish_cancelled(record, "cancelled while queued")
            return self._records[subagent_id]
        if record.status is SubagentStatus.RUNNING:
            runtime = self._runtimes.get(subagent_id)
            if runtime is not None:
                runtime.control.cancel()
            self.approvals.cancel(subagent_id)
            task = self._tasks.get(subagent_id)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            return self._records[subagent_id]
        return record

    async def integrate(
        self,
        subagent_id: str,
        verification_commands: tuple[str, ...] = (),
    ) -> SubagentResult:
        record = self._records.get(subagent_id)
        if (
            record is None
            or record.status is not SubagentStatus.COMPLETED
            or record.spec.kind is not SubagentTaskKind.WRITE
            or record.commit is None
            or subagent_id not in self._leases
        ):
            raise SubagentCoordinatorError(
                "not_integratable", "subagent must be a completed clean write task"
            )
        integration = await self.worktrees.integrate(self._leases[subagent_id], self.workspace)
        if not integration.integrated:
            conflict = await self._transition(record, SubagentStatus.CONFLICT)
            result = replace(
                self._results[subagent_id],
                status=SubagentStatus.CONFLICT,
                error_category="integration_conflict",
                error_message=integration.error_message,
            )
            self._results[subagent_id] = result
            await self._persist_result(result)
            await self._publish_record_event(
                conflict,
                SubagentConflict,
                summary="integration conflict",
                conflict_files=integration.conflict_files,
                message=integration.error_message or "cherry-pick conflict",
            )
            return result

        verification = await self.verification.run(
            verification_commands,
            workspace=self.workspace,
            run_id=self.parent_run_id,
        )
        if any(not item.passed for item in verification):
            failed = await self._transition(record, SubagentStatus.INTEGRATION_FAILED)
            result = replace(
                self._results[subagent_id],
                status=SubagentStatus.INTEGRATION_FAILED,
                verification=verification,
                error_category="parent_verification_failed",
                error_message="parent verification failed after integration",
            )
            self._results[subagent_id] = result
            await self._persist_result(result)
            await self._publish_record_event(
                failed,
                SubagentFailed,
                summary="parent verification failed",
                message="parent verification failed after integration",
                category="parent_verification_failed",
                usage=result.usage,
            )
            return result

        integrated = await self._transition(record, SubagentStatus.INTEGRATED)
        result = replace(
            self._results[subagent_id],
            status=SubagentStatus.INTEGRATED,
            verification=verification,
        )
        self._results[subagent_id] = result
        await self._persist_result(result)
        await self._publish_record_event(
            integrated,
            SubagentIntegrated,
            summary="subagent commit integrated",
            commit=integration.parent_commit_after,
            verification=tuple(item.command for item in verification),
        )
        cleanup = await self.worktrees.cleanup(
            self._leases[subagent_id],
            self.workspace,
            integrated=True,
        )
        await self._publish_record_event(
            integrated,
            SubagentCleanup,
            summary="Worktree cleanup",
            removed=cleanup.removed,
            retained_path=None if cleanup.retained_path is None else str(cleanup.retained_path),
            reason=cleanup.reason,
        )
        return result

    async def shutdown(self, reason: str) -> None:
        self._closed = True
        active = [
            record.subagent_id
            for record in self.list()
            if record.status in {SubagentStatus.QUEUED, SubagentStatus.RUNNING}
        ]
        await asyncio.gather(*(self.cancel(subagent_id) for subagent_id in active))

    async def recover(self) -> tuple[SubagentRecord, ...]:
        latest: dict[str, SubagentRecord] = {}
        persisted_results: dict[str, dict[str, object]] = {}
        for stored in self.event_bus.session_store.load_records():
            if stored.record_type == "subagent_record":
                record = subagent_record_from_dict(stored.payload)
                latest[record.subagent_id] = record
            elif stored.record_type == "subagent_result":
                persisted_results[str(stored.payload.get("subagent_id"))] = stored.payload
        self._records = latest
        for record in self.list():
            self._completion.setdefault(
                record.subagent_id, asyncio.get_running_loop().create_future()
            )
            if record.status in {SubagentStatus.QUEUED, SubagentStatus.RUNNING}:
                await self._finish_cancelled(record, "interrupted before recovery")
                continue
            raw_result = persisted_results.get(record.subagent_id)
            if raw_result is not None:
                raw_usage = raw_result.get("usage")
                usage_values: Mapping[str, object] = (
                    cast(Mapping[str, object], raw_usage)
                    if isinstance(raw_usage, Mapping)
                    else dict[str, object]()
                )
                usage = Usage(
                    input_tokens=int(str(usage_values.get("input_tokens", 0))),
                    output_tokens=int(str(usage_values.get("output_tokens", 0))),
                    cache_read_tokens=int(str(usage_values.get("cache_read_tokens", 0))),
                    cache_write_tokens=int(str(usage_values.get("cache_write_tokens", 0))),
                )
                result = SubagentResult(
                    record.subagent_id,
                    record.spec.task_name,
                    record.status,
                    str(raw_result.get("summary", "")),
                    commit=record.commit,
                    usage=usage,
                    error_category=(
                        None
                        if raw_result.get("error_category") is None
                        else str(raw_result.get("error_category"))
                    ),
                    error_message=(
                        None
                        if raw_result.get("error_message") is None
                        else str(raw_result.get("error_message"))
                    ),
                )
                self._results[record.subagent_id] = result
                future = self._completion[record.subagent_id]
                if not future.done():
                    future.set_result(result)
            if record.worktree_path is not None:
                try:
                    lease = await self.worktrees.recover(record)
                except Exception:
                    lease = None
                if lease is not None:
                    self._leases[record.subagent_id] = lease
        return self.list()
