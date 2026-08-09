from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict

from windcode.config import PermissionMode
from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.policy import ApprovalChoice, PolicyDecision, PolicyEngine, PolicyRequest
from windcode.runtime.scheduler import PolicyConstraints, ScheduledCall, ToolScheduler
from windcode.tools import ToolRegistry


class SideEffectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class SideEffectTool:
    name = "side_effect"
    description = "Create a file for extension policy integration tests."
    input_model = SideEffectInput
    effects = frozenset({ToolEffect.WORKSPACE_WRITE})

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        parsed = cast(SideEffectInput, arguments)
        (context.workspace / parsed.path).write_text("executed", encoding="utf-8")
        return ToolResult("original")


@pytest.mark.asyncio
async def test_extension_rejection_precedes_approval_and_side_effect(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(SideEffectTool())
    approvals = 0

    async def before_policy(_call: ScheduledCall, _context: ToolContext) -> PolicyConstraints:
        return PolicyConstraints(reject_reason="blocked by hook")

    async def approve(_request: PolicyRequest, _decision: PolicyDecision) -> ApprovalChoice:
        nonlocal approvals
        approvals += 1
        return ApprovalChoice.ALLOW_ONCE

    scheduler = ToolScheduler(
        registry,
        PolicyEngine(PermissionMode.DEFAULT, sandbox_enabled=False),
        approval_handler=approve,
        before_policy=before_policy,
    )

    result = (
        await scheduler.execute(
            (ScheduledCall("call", "side_effect", {"path": "sentinel"}),),
            ToolContext(tmp_path, "run", lambda: False),
        )
    )[0].result

    assert result.data["error"] == "extension_rejected"
    assert approvals == 0
    assert not (tmp_path / "sentinel").exists()


@pytest.mark.asyncio
async def test_post_extension_observer_cannot_replace_tool_result(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(SideEffectTool())
    observed: list[ToolResult] = []

    async def after_execute(
        _call: ScheduledCall, _request: PolicyRequest, result: ToolResult
    ) -> None:
        observed.append(result)
        result = ToolResult("replacement")
        del result

    scheduler = ToolScheduler(
        registry,
        PolicyEngine(PermissionMode.FULL_ACCESS, sandbox_enabled=False),
        after_execute=after_execute,
    )
    result = (
        await scheduler.execute(
            (ScheduledCall("call", "side_effect", {"path": "sentinel"}),),
            ToolContext(tmp_path, "run", lambda: False),
        )
    )[0].result

    assert result.output == "original"
    assert observed == [result]
    assert (tmp_path / "sentinel").read_text(encoding="utf-8") == "executed"


@pytest.mark.asyncio
async def test_plugin_manifest_effects_are_added_before_policy(tmp_path: Path) -> None:
    from windcode.extensions.models import ExtensionSnapshot
    from windcode.extensions.plugins.manifest import PluginManifest
    from windcode.extensions.runtime import RunExtensions

    class EmptyCredentialStore:
        def get(self, credential_id: str) -> str | None:
            del credential_id
            return None

        def set(self, credential_id: str, secret: str) -> None:
            del credential_id, secret

        def delete(self, credential_id: str) -> None:
            del credential_id

    manifest = PluginManifest(
        1,
        "guard",
        "Guard",
        "1.0.0",
        "*",
        False,
        (),
        (),
        (),
        (),
        ("process", "network"),
        ("api.example.com",),
        False,
        tmp_path,
    )
    snapshot = ExtensionSnapshot(
        1,
        "fingerprint",
        definitions={"plugin:guard/plugin/guard": manifest},
    )
    runtime = RunExtensions.create(
        snapshot,
        session_id="session",
        run_id="run",
        credential_store=EmptyCredentialStore(),
        max_content_bytes=1024,
        connect_timeout=1,
        call_timeout=1,
    )

    constraints = await runtime.before_policy(
        ScheduledCall("call", "shell", {"command": "true"}, origin="plugin:guard/hook"),
        ToolContext(tmp_path, "run", lambda: False),
    )

    assert constraints.additional_effects == {ToolEffect.PROCESS, ToolEffect.NETWORK}
