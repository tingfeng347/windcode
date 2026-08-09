from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from windcode.config import PermissionMode
from windcode.domain.subagents import SubagentRecord
from windcode.runtime.control import RunControl
from windcode.runtime.event_bus import EventBus
from windcode.runtime.loop import AgentLoop


@dataclass(slots=True)
class ChildRuntime:
    record: SubagentRecord
    control: RunControl
    event_bus: EventBus
    loop: AgentLoop
    workspace: Path
    prompt: str
    force_plan_on_permission_update: bool = False

    def set_parent_permission(
        self,
        previous: PermissionMode,
        selected: PermissionMode,
    ) -> None:
        effective = PermissionMode.PLAN if self.force_plan_on_permission_update else selected
        self.loop.scheduler.policy.set_mode(effective)
        self.loop.system_prompt = self.loop.system_prompt.replace(
            f"权限模式: {previous.value}.",
            f"权限模式: {effective.value}.",
        )
