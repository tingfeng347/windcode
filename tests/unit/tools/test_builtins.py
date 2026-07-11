import json

from windcode.tools import create_builtin_registry


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
