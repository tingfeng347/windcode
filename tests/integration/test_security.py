from pathlib import Path

import pytest

from windcode.config import PermissionMode
from windcode.domain.tools import ToolEffect
from windcode.policy import PolicyAction, PolicyEngine, PolicyRequest
from windcode.sandbox import BubblewrapSandbox, SandboxStatus
from windcode.tools.filesystem import require_workspace_path


def request(*effects: ToolEffect) -> PolicyRequest:
    return PolicyRequest(
        request_id="request",
        call_id="call",
        tool_name="tool",
        effects=frozenset(effects),
        summary="operation",
    )


def test_symlink_escape_is_rejected_before_policy_or_execution(tmp_path: Path) -> None:
    outside = tmp_path.parent / "security-outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        require_workspace_path(tmp_path, "link/secret")


def test_missing_sandbox_elevates_shell_approval() -> None:
    engine = PolicyEngine(
        PermissionMode.FULL_ACCESS,
        sandbox_enabled=True,
        sandbox_available=False,
    )
    assert engine.evaluate(request(ToolEffect.PROCESS)).action is PolicyAction.ASK


def test_sandbox_arguments_block_network_and_outside_workspace(tmp_path: Path) -> None:
    sandbox = BubblewrapSandbox(tmp_path, SandboxStatus(True, Path("/usr/bin/bwrap")))
    arguments = sandbox.wrap(("bash", "-lc", "true"))
    assert "--unshare-net" in arguments
    assert ("--bind", str(tmp_path), str(tmp_path)) == arguments[6:9]
