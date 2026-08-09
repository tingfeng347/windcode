from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from windcode.config import AppConfig
from windcode.domain.events import RunRequest
from windcode.domain.messages import Message, heal_dangling_tool_calls, message_from_dict
from windcode.extensions import ExtensionSnapshot
from windcode.providers import ModelTarget
from windcode.runtime.resources import RunResources
from windcode.runtime.subagents.factory import ChildRunScope
from windcode.sessions import ArtifactStore, SessionStore, ancestor_chain
from windcode.tools import ToolRegistry


@dataclass(frozen=True, slots=True)
class ParentRunPreparation:
    workspace: Path
    existing_session: bool
    session: SessionStore
    initial_messages: tuple[Message, ...]
    run_id: str
    artifact_store: ArtifactStore


class RunBuilder:
    def __init__(
        self,
        config: AppConfig,
        *,
        state_root: Path,
        model_chain: Callable[[str | None], tuple[ModelTarget, ...]],
    ) -> None:
        self.config = config
        self.state_root = state_root
        self.model_chain = model_chain

    @staticmethod
    def _summary(prompt: str, *, limit: int = 60) -> str:
        summary = " ".join(prompt.split())
        if len(summary) <= limit:
            return summary
        return summary[: limit - 3].rstrip() + "..."

    def prepare_parent(self, request: RunRequest) -> ParentRunPreparation:
        workspace = request.workspace.expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError(f"workspace is not a directory: {workspace}")
        sessions_root = self.state_root / "sessions"
        existing = (
            request.session_id is not None
            and (sessions_root / request.session_id / "meta.json").exists()
        )
        if existing:
            assert request.session_id is not None
            session = SessionStore.open(sessions_root, request.session_id)
        else:
            session = SessionStore.create(sessions_root, request.session_id)
        if not session.metadata.summary:
            session.set_summary(self._summary(request.prompt))
        initial_messages: tuple[Message, ...] = ()
        if existing and session.metadata.head_record_id is not None:
            records = ancestor_chain(session.load_records(), session.metadata.head_record_id)
            initial_messages = heal_dangling_tool_calls(
                tuple(
                    message_from_dict(record.payload)
                    for record in records
                    if record.record_type == "conversation_message"
                )
            )
        return ParentRunPreparation(
            workspace,
            existing,
            session,
            initial_messages,
            uuid4().hex,
            ArtifactStore(session.session_dir),
        )

    def resources(self, preparation: ParentRunPreparation) -> RunResources:
        return RunResources.create(
            session=preparation.session,
            run_id=preparation.run_id,
            state_root=self.state_root,
            artifact_store=preparation.artifact_store,
            trace_config=self.config.trace,
            context_config=self.config.context,
        )

    def child_scope(
        self,
        parent_tools: ToolRegistry,
        extension_snapshot: ExtensionSnapshot,
        *,
        default_model: str | None,
    ) -> ChildRunScope:
        return ChildRunScope(
            config=self.config,
            state_root=self.state_root,
            parent_tools=parent_tools,
            model_chain=lambda model: self.model_chain(model or default_model),
            extension_snapshot=extension_snapshot,
        )
