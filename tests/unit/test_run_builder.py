from functools import partial
from pathlib import Path

import pytest

from windcode.auth import FileCredentialStore
from windcode.config import AppConfig
from windcode.domain.events import RunRequest
from windcode.domain.messages import Message, Role, TextBlock, message_to_dict
from windcode.extensions import ExtensionSnapshot
from windcode.runtime.run_builder import RunBuilder, RunExtensionState
from windcode.runtime.subagents.child_support import ChildRunSupport
from windcode.tools import ToolRegistry


def builder(
    state_root: Path,
    *,
    snapshot: ExtensionSnapshot | None = None,
) -> RunBuilder:
    return RunBuilder(
        AppConfig(),
        state_root=state_root,
        user_storage_root=state_root / "user",
        base_tools=ToolRegistry(),
        model_chain=lambda _model: (),
        extensions=RunExtensionState(
            snapshot or ExtensionSnapshot(0, "test"),
            FileCredentialStore(state_root / "auth.json"),
            None,
            None,
            {},
            set(),
            24,
        ),
    )


def test_prepare_parent_rejects_invalid_workspace_synchronously(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="workspace is not a directory"):
        builder(tmp_path / "state").prepare_parent(RunRequest("task", tmp_path / "missing"))


def test_prepare_parent_reopens_session_and_restores_history(tmp_path: Path) -> None:
    run_builder = builder(tmp_path / "state")
    first = run_builder.prepare_parent(RunRequest("first task", tmp_path, session_id="session"))
    first.session.append(
        "conversation_message",
        message_to_dict(Message(Role.USER, (TextBlock("remembered"),))),
        durable=True,
    )

    resumed = run_builder.prepare_parent(RunRequest("second task", tmp_path, session_id="session"))

    assert not first.existing_session
    assert resumed.existing_session
    assert resumed.session.metadata.summary == "first task"
    assert len(resumed.initial_messages) == 1
    restored = resumed.initial_messages[0]
    assert restored.role is Role.USER
    assert restored.content == (TextBlock("remembered"),)
    assert resumed.run_id != first.run_id
    assert run_builder.resources(resumed).event_bus.session_store is resumed.session


def test_child_scope_pins_snapshot_and_parent_tool_view(tmp_path: Path) -> None:
    tools = ToolRegistry()
    snapshot = ExtensionSnapshot(7, "snapshot")
    run_builder = builder(tmp_path / "state", snapshot=snapshot)

    prepare_child = run_builder.bind_child(tools, default_model="parent-model")

    assert isinstance(prepare_child, partial)
    support = prepare_child.keywords["support"]
    assert isinstance(support, ChildRunSupport)
    assert support.parent_tools is tools
    assert support.extension_snapshot is snapshot
    assert prepare_child.keywords["default_model"] == "parent-model"
