from __future__ import annotations

from typing import Protocol

from windcode.domain.subagents import (
    CollaborationRequest,
    CollaborationResult,
    SubagentRecord,
    SubagentResult,
    SubagentTaskSpec,
)


class SubagentOperations(Protocol):
    async def spawn(self, specs: tuple[SubagentTaskSpec, ...]) -> tuple[SubagentRecord, ...]: ...

    def list(self) -> tuple[SubagentRecord, ...]: ...

    async def wait(self, subagent_id: str) -> SubagentResult: ...

    async def cancel(self, subagent_id: str) -> SubagentRecord: ...

    async def integrate(
        self,
        subagent_id: str,
        verification_commands: tuple[str, ...] = (),
    ) -> SubagentResult: ...

    async def collaborate(self, request: CollaborationRequest) -> CollaborationResult: ...
