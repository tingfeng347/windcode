from pathlib import Path

import pytest

from windcode.extensions.hooks.loader import load_hook_definition
from windcode.extensions.hooks.models import HookEvent, RejectAction


def test_loads_strict_pre_execution_reject(tmp_path: Path) -> None:
    (tmp_path / "hook.toml").write_text(
        "id='guard'\nevent='tool_before_policy'\npriority=1\n[action]\ntype='reject'\nreason='blocked'\n"
    )
    hook = load_hook_definition(tmp_path, "hook.toml", source_id="plugin:x/guard")
    assert hook.matcher.event is HookEvent.TOOL_BEFORE_POLICY
    assert isinstance(hook.action, RejectAction)


def test_reject_is_invalid_for_post_event(tmp_path: Path) -> None:
    (tmp_path / "hook.toml").write_text(
        "id='guard'\nevent='tool_after'\n[action]\ntype='reject'\nreason='late'\n"
    )
    with pytest.raises(ValueError, match="before tool policy"):
        load_hook_definition(tmp_path, "hook.toml", source_id="x")


def test_permission_hook_cannot_claim_to_reject_after_policy(tmp_path: Path) -> None:
    (tmp_path / "hook.toml").write_text(
        "id='guard'\nevent='permission_request'\n[action]\ntype='reject'\nreason='too late'\n"
    )

    with pytest.raises(ValueError, match="before tool policy"):
        load_hook_definition(tmp_path, "hook.toml", source_id="x")
