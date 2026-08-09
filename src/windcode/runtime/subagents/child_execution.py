from windcode.domain.subagents import SubagentRecord
from windcode.domain.tools import ToolContext
from windcode.policy import ApprovalChoice, PolicyDecision, PolicyRequest
from windcode.runtime.control import BudgetExceeded, RunBudgets, RunControl
from windcode.runtime.loop import (
    AgentBlocked,
    AgentLoop,
    ContextWindow,
    InboundMessageSource,
    ModelSession,
    RunIdentity,
    RunJournal,
    RunObservers,
    ToolRuntime,
)
from windcode.runtime.scheduler import ScheduledCall, ScheduledResult, ToolScheduler
from windcode.runtime.subagents.approvals import ApprovalRouter
from windcode.runtime.subagents.budgets import AggregateBudget, AggregateBudgetExceeded


class AggregateRunControl(RunControl):
    def __init__(self, budgets: RunBudgets, aggregate: AggregateBudget) -> None:
        super().__init__(budgets)
        self.aggregate = aggregate

    def check(self) -> None:
        super().check()
        try:
            self.aggregate.check_runtime_nowait()
        except AggregateBudgetExceeded as exc:
            raise BudgetExceeded(f"aggregate_{exc.budget}") from exc

    def start_model_step(self) -> int:
        try:
            self.aggregate.consume_model_step_nowait()
        except AggregateBudgetExceeded as exc:
            raise BudgetExceeded(f"aggregate_{exc.budget}") from exc
        return super().start_model_step()

    def reserve_tool_calls(self, count: int) -> None:
        try:
            self.aggregate.consume_tool_calls_nowait(count)
        except AggregateBudgetExceeded as exc:
            raise BudgetExceeded(f"aggregate_{exc.budget}") from exc
        super().reserve_tool_calls(count)


class ChildToolScheduler(ToolScheduler):
    async def execute(
        self,
        calls: tuple[ScheduledCall, ...],
        context: ToolContext,
    ) -> tuple[ScheduledResult, ...]:
        if any(call.tool_name == "ask_user" for call in calls):
            raise AgentBlocked("subagents cannot ask the user directly; clarification is required")
        return await super().execute(calls, context)


class ChildAgentLoop(AgentLoop):
    def __init__(
        self,
        *,
        record: SubagentRecord,
        approval_router: ApprovalRouter,
        identity: RunIdentity,
        model: ModelSession,
        tools: ToolRuntime,
        journal: RunJournal,
        context: ContextWindow | None = None,
        observers: RunObservers | None = None,
        inbound_message_source: InboundMessageSource | None = None,
    ) -> None:
        self.subagent_record = record
        self.approval_router = approval_router
        super().__init__(
            identity=identity,
            model=model,
            tools=tools,
            journal=journal,
            context=context,
            observers=observers,
            inbound_message_source=inbound_message_source,
        )

    async def _approval_handler(
        self,
        request: PolicyRequest,
        decision: PolicyDecision,
    ) -> ApprovalChoice:
        return await self.approval_router.request(
            self.subagent_record.subagent_id,
            self.subagent_record.spec.role,
            request,
            decision,
        )

    async def _request_user(self, payload: object) -> object:
        del payload
        raise AgentBlocked("subagents cannot ask the user directly; clarification is required")
