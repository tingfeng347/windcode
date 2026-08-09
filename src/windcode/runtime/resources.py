from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from windcode.config import ContextConfig, TraceConfig
from windcode.context import TokenEstimator
from windcode.observability import TraceStore
from windcode.runtime.event_bus import EventBus
from windcode.sessions import ArtifactStore, SessionStore


@dataclass(frozen=True, slots=True)
class RunResources:
    """Run-local persistence and context resources with one construction policy."""

    artifact_store: ArtifactStore
    trace_store: TraceStore
    event_bus: EventBus
    token_estimator: TokenEstimator

    @classmethod
    def create(
        cls,
        *,
        session: SessionStore,
        run_id: str,
        state_root: Path,
        artifact_store: ArtifactStore,
        trace_config: TraceConfig,
        context_config: ContextConfig,
    ) -> RunResources:
        trace_store = TraceStore(
            run_id,
            root=state_root / "traces",
            enabled=trace_config.enabled,
            include_tool_arguments=trace_config.include_tool_arguments,
            include_transient_events=trace_config.include_transient_events,
            retention_days=trace_config.retention_days,
            max_total_mb=trace_config.max_total_mb,
        )
        return cls(
            artifact_store=artifact_store,
            trace_store=trace_store,
            event_bus=EventBus(session, trace_store),
            token_estimator=TokenEstimator(
                context_config.window_tokens,
                compaction_threshold=context_config.compaction_threshold,
            ),
        )
