from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from windcode.config import PermissionMode
from windcode.context import CHECKPOINT_SECTIONS, TokenEstimator
from windcode.domain.events import ContextCompacted
from windcode.domain.messages import TextBlock
from windcode.domain.models import ModelCompleted, ModelEvent, ModelRequest, StopReason, TextDelta
from windcode.observability import TraceStore
from windcode.policy import PolicyEngine
from windcode.providers import ModelTarget
from windcode.runtime import AgentLoop, EventBus, RunControl, ToolScheduler
from windcode.sessions import ArtifactStore, SessionStore
from windcode.tools import ToolRegistry


class CompactionTransport:
    name = "scripted"

    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        last_block = request.messages[-1].content[0]
        if isinstance(last_block, TextBlock) and "结构化检查点" in last_block.text:
            if self.valid:
                checkpoint = "\n".join(f"## {section}\ncontent" for section in CHECKPOINT_SECTIONS)
                yield TextDelta(checkpoint)
            else:
                yield TextDelta("invalid checkpoint")
            yield ModelCompleted(StopReason.STOP)
            return
        yield TextDelta("done")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


def build_loop(tmp_path: Path, transport: CompactionTransport) -> tuple[AgentLoop, EventBus]:
    session = SessionStore.create(tmp_path / "sessions", "session")
    bus = EventBus(session, TraceStore("run", root=tmp_path / "traces"))
    scheduler = ToolScheduler(
        ToolRegistry(), PolicyEngine(PermissionMode.FULL_ACCESS, sandbox_enabled=False)
    )
    return (
        AgentLoop(
            session_id="session",
            run_id="run",
            model_chain=(ModelTarget("scripted", "model", transport),),
            scheduler=scheduler,
            control=RunControl(),
            event_bus=bus,
            system_prompt="system",
            token_estimator=TokenEstimator(
                1_024, compaction_threshold=0.2, reserved_output_tokens=100
            ),
            artifact_store=ArtifactStore(session.session_dir),
        ),
        bus,
    )


@pytest.mark.asyncio
async def test_compacts_then_continues_the_run(tmp_path: Path) -> None:
    transport = CompactionTransport()
    loop, bus = build_loop(tmp_path, transport)

    result = await loop.run("x" * 1_000, tmp_path)
    events = [event async for event in bus.subscribe()]

    assert result.final_text == "done"
    assert any(isinstance(event, ContextCompacted) for event in events)
    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_invalid_checkpoint_keeps_original_model_view(tmp_path: Path) -> None:
    transport = CompactionTransport(valid=False)
    loop, bus = build_loop(tmp_path, transport)

    result = await loop.run("x" * 1_000, tmp_path)
    events = [event async for event in bus.subscribe()]

    assert result.final_text == "done"
    assert not any(isinstance(event, ContextCompacted) for event in events)
    original = transport.requests[-1].messages[0].content[0]
    assert isinstance(original, TextBlock)
    assert original.text == "x" * 1_000
