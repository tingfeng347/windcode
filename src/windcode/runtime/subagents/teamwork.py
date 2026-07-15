from __future__ import annotations

import asyncio
from uuid import uuid4

from windcode.domain.subagents import (
    CollaborationContribution,
    CollaborationMode,
    CollaborationParticipant,
    CollaborationRequest,
    CollaborationResult,
    SubagentResult,
    SubagentRole,
    SubagentStatus,
    SubagentTaskKind,
    SubagentTaskSpec,
)
from windcode.runtime.subagents.coordinator import SubagentCoordinator, SubagentCoordinatorError

_NEGOTIATION_MARKERS = (
    "协商",
    "讨论",
    "辩论",
    "质疑",
    "评审",
    "争议",
    "共识",
    "tradeoff",
    "debate",
    "challenge",
    "review",
    "consensus",
)
_DIVISION_MARKERS = (
    "分工",
    "并行",
    "分别",
    "模块",
    "拆分",
    "合并",
    "实现",
    "调研",
    "divide",
    "parallel",
    "implement",
    "module",
    "merge",
    "research",
)
_MAX_CONTEXT_CHARS = 12_000


def infer_collaboration_mode(text: str) -> CollaborationMode:
    normalized = text.casefold()
    negotiation = any(marker in normalized for marker in _NEGOTIATION_MARKERS)
    division = any(marker in normalized for marker in _DIVISION_MARKERS)
    if negotiation and division:
        return CollaborationMode.HYBRID
    if negotiation:
        return CollaborationMode.NEGOTIATION
    if division:
        return CollaborationMode.DIVISION
    return CollaborationMode.HYBRID


def _resolved_mode(request: CollaborationRequest) -> CollaborationMode:
    if request.mode is not CollaborationMode.AUTO:
        return request.mode
    assignments = "\n".join(participant.assignment for participant in request.participants)
    return infer_collaboration_mode(f"{request.request}\n{request.context}\n{assignments}")


def _workflow(mode: CollaborationMode, rounds: int) -> str:
    if mode is CollaborationMode.NEGOTIATION:
        opening = (
            "First independently establish your assigned position with evidence, assumptions, and "
            "questions for other positions."
        )
        later = (
            "In every later round, directly address claims from all other participants, challenge "
            "weak evidence, answer objections, state concessions or position changes, and preserve "
            "unresolved disagreements."
        )
    elif mode is CollaborationMode.DIVISION:
        opening = (
            "First independently complete your assigned workstream and report evidence, artifacts, "
            "interfaces, dependencies, risks, and remaining work."
        )
        later = (
            "In every later round, inspect peer outputs, resolve dependencies and overlaps, review "
            "integration risks, fill gaps, and update your own deliverable."
        )
    else:
        opening = (
            "First independently complete your assigned workstream and establish the decisions or "
            "position it supports."
        )
        later = (
            "In every later round, review peer deliverables, challenge conflicting assumptions, "
            "resolve dependencies, concede valid objections, and update your contribution."
        )
    return (
        f"{opening}\n"
        "Submit that blind contribution with exchange_round(round_index=0). The tool is a barrier "
        "and returns every participant's round-0 contribution. Do not finish after round 0.\n"
        f"{later}\n"
        f"Then call exchange_round exactly once for each round_index from 1 through {rounds}. "
        "Each submission must be a substantive updated contribution based on the prior barrier "
        "result. Finish only after the final barrier returns."
    )


def _participant_spec(
    collaboration_id: str,
    mode: CollaborationMode,
    request: CollaborationRequest,
    participant: CollaborationParticipant,
) -> SubagentTaskSpec:
    peers = ", ".join(item.name for item in request.participants if item.name != participant.name)
    return SubagentTaskSpec(
        task_name=f"collab_{collaboration_id}_{participant.name}",
        role=participant.role,
        kind=participant.kind,
        goal=f"Contribute as {participant.name} to: {request.request}",
        context=(
            f"Resolved work mode: {mode.value}\n"
            f"Shared request:\n{request.request}\n\n"
            f"Shared context:\n{request.context or '(none)'}\n\n"
            f"Your stable participant identity: {participant.name}\n"
            f"Your assignment or perspective: {participant.assignment}\n"
            f"Other participants: {peers}\n\n"
            f"Mandatory coordination protocol:\n{_workflow(mode, request.rounds)}"
        ),
        expected_output=(
            "A final participant report that reflects all coordination rounds, attributes peer "
            "inputs used, and explicitly lists unresolved dependencies or disagreements."
        ),
        verification=(
            "Call exchange_round for every required round before returning the final response.",
            "Ground claims in workspace evidence or label them as assumptions.",
            "Attribute peer contributions and do not claim false consensus.",
        ),
        allowed_tools=participant.allowed_tools,
        model=participant.model,
        requires_network=participant.requires_network,
        peer_collaboration=False,
        coordination_id=collaboration_id,
        coordination_participant=participant.name,
        coordination_rounds=request.rounds,
    )


def _transcript(contributions: tuple[CollaborationContribution, ...]) -> str:
    return "\n\n".join(
        f"## Round {item.round_index} — {item.participant_name}\n"
        f"{item.content[:_MAX_CONTEXT_CHARS]}"
        for item in contributions
    )


def _participant_results(results: tuple[SubagentResult, ...]) -> str:
    return "\n".join(
        f"- {result.task_name}: status={result.status.value}, commit={result.commit}, "
        f"changed_files={list(result.changed_files)}"
        for result in results
    )


def _synthesis_spec(
    collaboration_id: str,
    mode: CollaborationMode,
    request: CollaborationRequest,
    contributions: tuple[CollaborationContribution, ...],
    results: tuple[SubagentResult, ...],
) -> SubagentTaskSpec:
    return SubagentTaskSpec(
        task_name=f"collab_{collaboration_id}_synthesis",
        role=SubagentRole.VERIFIER,
        kind=SubagentTaskKind.READ,
        goal=f"Independently synthesize the {mode.value} collaboration: {request.request}",
        context=(
            f"Shared context:\n{request.context or '(none)'}\n\n"
            f"Complete coordinated transcript:\n{_transcript(contributions)}\n\n"
            f"Participant delivery metadata:\n{_participant_results(results)}\n\n"
            "You are an independent coordinator, not a participant. Attribute contributions, "
            "distinguish evidence from assertion, preserve unresolved disagreement, and identify "
            "integration dependencies or conflicting file changes."
        ),
        expected_output=(
            f"{request.synthesis_instructions}\nFor negotiation, retain minority positions and "
            "evidence on each side. For division, map deliverables, ownership, dependencies, gaps, "
            "and an integration order. For hybrid work, do both."
        ),
        verification=(
            "Trace every major conclusion to labeled participant rounds.",
            "Do not report completion for failed, missing, or conflicting workstreams.",
        ),
        peer_collaboration=False,
    )


async def run_collaboration(
    coordinator: SubagentCoordinator,
    request: CollaborationRequest,
) -> CollaborationResult:
    collaboration_id = uuid4().hex[:6]
    mode = _resolved_mode(request)
    participant_count = len(request.participants)
    available_tasks = coordinator.config.max_tasks - len(coordinator.list())
    if participant_count + 1 > available_tasks:
        raise SubagentCoordinatorError(
            "collaboration_capacity_exceeded",
            f"collaboration requires {participant_count + 1} tasks but only "
            f"{available_tasks} remain",
        )
    if participant_count > coordinator.available_concurrency():
        raise SubagentCoordinatorError(
            "collaboration_concurrency_exceeded",
            f"all {participant_count} participants must run concurrently, but only "
            f"{coordinator.available_concurrency()} slots are available",
        )

    await coordinator.register_coordination_session(
        collaboration_id,
        mode,
        tuple(participant.name for participant in request.participants),
        request.rounds,
    )
    specs = tuple(
        _participant_spec(collaboration_id, mode, request, participant)
        for participant in request.participants
    )
    try:
        records = await coordinator.spawn(specs)
        results = tuple(
            await asyncio.gather(*(coordinator.wait(record.subagent_id) for record in records))
        )
        contributions = coordinator.coordination_contributions(collaboration_id)
        required_contributions = participant_count * (request.rounds + 1)
        failed = [result for result in results if result.status is not SubagentStatus.COMPLETED]
        if failed or len(contributions) != required_contributions:
            reason = (
                f"participant {failed[0].task_name} ended with {failed[0].status.value}"
                if failed
                else f"expected {required_contributions} contributions, got {len(contributions)}"
            )
            return CollaborationResult(
                collaboration_id=collaboration_id,
                request=request.request,
                mode=mode,
                status="failed",
                contributions=contributions,
                participant_results=results,
                error_category="collaboration_incomplete",
                error_message=reason,
            )

        (synthesizer,) = await coordinator.spawn(
            (_synthesis_spec(collaboration_id, mode, request, contributions, results),)
        )
        synthesis = await coordinator.wait(synthesizer.subagent_id)
        if synthesis.status is not SubagentStatus.COMPLETED:
            return CollaborationResult(
                collaboration_id=collaboration_id,
                request=request.request,
                mode=mode,
                status="failed",
                contributions=contributions,
                participant_results=results,
                synthesizer_subagent_id=synthesis.subagent_id,
                error_category="collaboration_synthesis_failed",
                error_message=synthesis.error_message or synthesis.summary,
            )
        return CollaborationResult(
            collaboration_id=collaboration_id,
            request=request.request,
            mode=mode,
            status="completed",
            contributions=contributions,
            participant_results=results,
            synthesis=synthesis.summary,
            synthesizer_subagent_id=synthesis.subagent_id,
        )
    except SubagentCoordinatorError as exc:
        return CollaborationResult(
            collaboration_id=collaboration_id,
            request=request.request,
            mode=mode,
            status="failed",
            contributions=coordinator.coordination_contributions(collaboration_id),
            error_category=exc.category,
            error_message=str(exc),
        )
