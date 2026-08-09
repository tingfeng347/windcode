from pathlib import Path

from windcode.config import ContextConfig, TraceConfig
from windcode.runtime.resources import RunResources
from windcode.sessions import ArtifactStore, SessionStore


def test_creates_independent_run_resources_from_shared_policy(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    first_session = SessionStore.create(state_root / "sessions", "first")
    second_session = SessionStore.create(state_root / "sessions", "second")
    trace_config = TraceConfig(include_tool_arguments=True, include_transient_events=True)
    context_config = ContextConfig(window_tokens=8_000, compaction_threshold=0.5)

    first = RunResources.create(
        session=first_session,
        run_id="run-first",
        state_root=state_root,
        artifact_store=ArtifactStore(first_session.session_dir),
        trace_config=trace_config,
        context_config=context_config,
    )
    second = RunResources.create(
        session=second_session,
        run_id="run-second",
        state_root=state_root,
        artifact_store=ArtifactStore(second_session.session_dir),
        trace_config=trace_config,
        context_config=context_config,
    )

    assert first.event_bus is not second.event_bus
    assert first.event_bus.session_store is first_session
    assert second.event_bus.session_store is second_session
    assert first.trace_store.path != second.trace_store.path
    assert first.event_bus.trace_store is first.trace_store
    assert first.artifact_store.artifacts_dir == first_session.session_dir / "artifacts"
    assert first.token_estimator.window_tokens == 8_000
    assert first.token_estimator.compaction_threshold == 0.5
