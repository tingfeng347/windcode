import json
from typing import cast

from windcode.runtime.subagents.coordinator import SubagentCoordinator
from windcode.tools import add_subagent_tools, create_builtin_registry


def test_registers_eight_stable_json_serializable_tools() -> None:
    schemas = create_builtin_registry().schemas()
    assert [schema.name for schema in schemas] == [
        "read_file",
        "write_file",
        "edit_file",
        "apply_patch",
        "glob",
        "grep",
        "shell",
        "ask_user",
    ]
    json.dumps([schema.parameters for schema in schemas])


def test_effects_match_builtin_behavior() -> None:
    registry = create_builtin_registry()
    assert {effect.value for effect in registry.get("read_file").effects} == {"read"}
    assert {effect.value for effect in registry.get("shell").effects} == {"process"}


def test_root_subagent_tools_are_added_to_a_clone_only() -> None:
    base = create_builtin_registry()
    root = base.clone()
    add_subagent_tools(root, cast(SubagentCoordinator, object()))
    assert base.names()[-1] == "ask_user"
    assert root.names()[-4:] == (
        "spawn_subagents",
        "list_subagents",
        "cancel_subagent",
        "integrate_subagent",
    )
