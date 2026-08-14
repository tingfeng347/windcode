from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from windcode.config import MemoryConfig
from windcode.domain.events import MemoryEvent, RunRequest, RunResult
from windcode.memory import (
    MemoryActivation,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryService,
    MemorySource,
    MemoryStatus,
    assess_core_project_fact,
    assess_experience,
    classify_memory_intent,
    explicitly_always_project_fact,
    has_explicit_memory_intent,
    is_project_fact,
    refine_memory,
    should_assess_experience,
)
from windcode.providers import ModelTarget
from windcode.runtime.event_bus import EventBus
from windcode.runtime.loop import RunIdentity
from windcode.tools import ToolRegistry, register_memory_tools


class RunMemory:
    """Owns run-scoped memory recall, tools, events, and completion extraction."""

    def __init__(
        self,
        config: MemoryConfig,
        *,
        state_root: Path,
        workspace: Path,
        request: RunRequest,
        identity: RunIdentity,
        registry: ToolRegistry,
        event_bus: EventBus,
        model: ModelTarget,
    ) -> None:
        self._config = config
        self._request = request
        self._identity = identity
        self._event_bus = event_bus
        self._model = model
        self._service = MemoryService(state_root, workspace) if config.enabled else None
        self._tool_memory_id: str | None = None
        if self._service is not None:
            register_memory_tools(
                registry,
                self._service,
                self._observe_tool,
                max_chars=config.recall_max_chars,
                user_prompt=request.prompt,
                source=self._source,
                model=model,
                enabled_kinds=self._enabled_kinds,
            )
        self.context = ""

    async def prepare(self) -> None:
        if self._service is None:
            return
        await self._service.migrate()
        self.context = await self._service.build_context(
            self._request.prompt,
            baseline_max_records=self._config.baseline_max_records,
            baseline_max_chars=self._config.baseline_max_chars,
            search_limit=self._config.recall_limit,
            search_max_chars=self._config.recall_max_chars,
        )

    @property
    def enabled(self) -> bool:
        return self._service is not None

    @property
    def _source(self) -> MemorySource:
        return MemorySource(self._identity.session_id, self._identity.run_id)

    @property
    def _enabled_kinds(self) -> frozenset[MemoryKind]:
        return frozenset(
            kind
            for kind, enabled in {
                MemoryKind.USER_PROFILE: self._config.user_profile_enabled,
                MemoryKind.PROJECT_KNOWLEDGE: self._config.project_knowledge_enabled,
                MemoryKind.EXPERIENCE: self._config.experience_enabled,
                MemoryKind.SOP: self._config.sop_enabled,
                MemoryKind.REFERENCE: self._config.reference_enabled,
            }.items()
            if enabled
        )

    async def _observe_tool(self, action: str, details: dict[str, object]) -> None:
        memory_id = details.get("memory_id")
        if action in {"activated", "candidate_created", "already_exists", "updated"} and isinstance(
            memory_id, str
        ):
            self._tool_memory_id = memory_id
        await self._event_bus.publish(
            MemoryEvent(
                event_id=uuid4().hex,
                session_id=self._identity.session_id,
                run_id=self._identity.run_id,
                turn=0,
                action=action,
                memory_id=memory_id if isinstance(memory_id, str) else None,
                memory_kind=str(details.get("kind", "")) or None,
                scope=str(details.get("scope", "")) or None,
                status=str(details.get("status", "")),
                details=details,
            ),
            durable=True,
        )

    async def publish_recalled(self) -> None:
        if not self.context:
            return
        await self._event_bus.publish(
            MemoryEvent(
                event_id=uuid4().hex,
                session_id=self._identity.session_id,
                run_id=self._identity.run_id,
                turn=0,
                action="recalled",
                status="active",
                details={"characters": len(self.context)},
            )
        )

    async def complete(self, result: RunResult) -> None:
        if not self._config.enabled or not self._config.extraction_enabled:
            return
        service = self._service
        if service is None:
            return
        explicit_experience_id = await self._extract_explicit_memory(service)
        if explicit_experience_id is not None:
            if result.verification:
                try:
                    experience = await service.store.get(explicit_experience_id)
                except KeyError:
                    # 记忆可能在本轮被 memory_delete 删除; 完成后处理应安全跳过。
                    return
                evidence = tuple(dict.fromkeys((*experience.evidence, *result.verification)))
                if evidence != experience.evidence:
                    await service.store.update(explicit_experience_id, evidence=evidence)
                await service.store.record_outcome(
                    explicit_experience_id, success=result.status == "completed"
                )
            return
        if self._config.experience_enabled and should_assess_experience(
            status=result.status,
            changed_files=result.changed_files,
            verification=result.verification,
        ):
            await self._extract_experience(service, result, explicit_experience_id)

    async def _extract_explicit_memory(self, service: MemoryService) -> str | None:
        explicit_experience_id: str | None = None
        if self._tool_memory_id is not None:
            try:
                tool_memory = await service.store.get(self._tool_memory_id)
            except KeyError:
                tool_memory = None
            if tool_memory is not None and tool_memory.kind is MemoryKind.EXPERIENCE:
                explicit_experience_id = self._tool_memory_id
        intent_kind = classify_memory_intent(self._request.prompt)
        if (
            self._tool_memory_id is not None
            or intent_kind is None
            or intent_kind not in self._enabled_kinds
        ):
            return explicit_experience_id
        project_fact = is_project_fact(self._request.prompt)
        scope = (
            MemoryScope.USER
            if intent_kind is MemoryKind.USER_PROFILE
            or (intent_kind is MemoryKind.REFERENCE and not project_fact)
            else MemoryScope.PROJECT
        )
        refined = await refine_memory(
            self._model,
            text=self._request.prompt,
            kind=intent_kind,
            max_output_tokens=self._config.extraction_max_output_tokens,
        )
        activation: MemoryActivation | None = None
        if intent_kind is MemoryKind.PROJECT_KNOWLEDGE:
            core = explicitly_always_project_fact(
                self._request.prompt
            ) or await assess_core_project_fact(
                self._model,
                text=self._request.prompt,
                max_output_tokens=min(256, self._config.extraction_max_output_tokens),
            )
            activation = MemoryActivation.ALWAYS if core else MemoryActivation.MANUAL
        priority = 60 if activation is MemoryActivation.ALWAYS else None
        candidate = await service.create_candidate(
            kind=intent_kind,
            scope=scope,
            title=refined.title,
            summary=refined.summary,
            body=refined.body,
            source=self._source,
            tags=refined.tags,
            evidence=(
                () if intent_kind is MemoryKind.SOP else (f"用户原话: {self._request.prompt}",)
            ),
            confidence=0.8,
            activation=activation,
            priority=priority,
        )
        if intent_kind is MemoryKind.SOP:
            saved = candidate
            action = "candidate_created"
            policy = "explicit_sop_candidate"
        elif candidate.conflicts_with:
            # 与已有 ACTIVE 记忆冲突: 保持 CANDIDATE 待用户确认, 不自动覆盖。
            saved = candidate
            action = "candidate_created"
            policy = "conflict_pending"
        else:
            saved = await service.store.transition(candidate.memory_id, MemoryStatus.ACTIVE)
            if intent_kind is MemoryKind.EXPERIENCE:
                explicit_experience_id = saved.memory_id
            action = "activated"
            policy = (
                "explicit_memory_intent"
                if has_explicit_memory_intent(self._request.prompt)
                else "stable_user_fact"
            )
        await self._publish_record(saved, action=action, details={"policy": policy})
        return explicit_experience_id

    async def _extract_experience(
        self,
        service: MemoryService,
        result: RunResult,
        explicit_experience_id: str | None,
    ) -> None:
        experience_text = (
            f"用户请求:\n{self._request.prompt}\n\n"
            f"变更文件:\n{chr(10).join(result.changed_files)}\n\n"
            f"任务结果:\n{result.final_text}"
        )[: self._config.extraction_max_chars]
        assessment = await assess_experience(
            self._model,
            text=experience_text,
            evidence=result.verification,
            max_output_tokens=self._config.extraction_max_output_tokens,
        )
        if not assessment.should_store or assessment.memory is None:
            return
        refined = assessment.memory
        duplicates = tuple(
            record
            for record in await service.list(
                statuses=(MemoryStatus.ACTIVE,),
            )
            if record.kind is MemoryKind.EXPERIENCE
            and (
                record.title.casefold() == refined.title.casefold()
                or record.summary.casefold() == refined.summary.casefold()
            )
        )
        if duplicates:
            existing = duplicates[0]
            if explicit_experience_id is not None:
                await service.store.delete(explicit_experience_id)
            evidence = tuple(dict.fromkeys((*existing.evidence, *result.verification)))
            await service.store.update(existing.memory_id, evidence=evidence)
            await service.store.record_outcome(existing.memory_id, success=True)
            return
        if explicit_experience_id is not None:
            experience = await service.store.update(
                explicit_experience_id,
                title=refined.title,
                summary=refined.summary,
                body=refined.body,
                tags=refined.tags,
                evidence=result.verification,
                confidence=0.8,
            )
        else:
            experience = await service.create_candidate(
                kind=MemoryKind.EXPERIENCE,
                scope=MemoryScope.PROJECT,
                title=refined.title,
                summary=refined.summary,
                body=refined.body,
                source=self._source,
                tags=refined.tags,
                evidence=result.verification,
                confidence=0.7,
            )
        verified = await service.store.transition(experience.memory_id, MemoryStatus.ACTIVE)
        await self._publish_record(
            verified,
            action="activated",
            details={"verified": True, "policy": "no_execution_no_memory"},
        )
        if self._config.sop_enabled and assessment.sop is not None:
            sop = assessment.sop
            candidate = await service.create_candidate(
                kind=MemoryKind.SOP,
                scope=MemoryScope.PROJECT,
                title=sop.title,
                summary=sop.summary,
                body=sop.body,
                source=self._source,
                tags=sop.tags,
                evidence=result.verification,
                confidence=0.7,
            )
            await self._publish_record(
                candidate,
                action="candidate_created",
                details={"verified": True, "policy": "experience_sop_candidate"},
            )

    async def _publish_record(
        self,
        record: MemoryRecord,
        *,
        action: str,
        details: dict[str, object],
    ) -> None:
        await self._event_bus.publish(
            MemoryEvent(
                event_id=uuid4().hex,
                session_id=self._identity.session_id,
                run_id=self._identity.run_id,
                turn=0,
                action=action,
                memory_id=record.memory_id,
                memory_kind=record.kind.value,
                scope=record.scope.value,
                status=record.status.value,
                details=details,
            ),
            durable=True,
        )
