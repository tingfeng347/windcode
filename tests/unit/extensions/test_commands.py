import pytest

from windcode.extensions.commands import build_command_catalog
from windcode.extensions.plugins.manifest import PluginCommand


def test_commands_are_sorted_and_keep_source() -> None:
    routes = build_command_catalog(
        (
            ("plugin:b", PluginCommand("z", "skill:z")),
            ("plugin:a", PluginCommand("a", "prompt:a")),
        )
    )
    assert [route.name for route in routes] == ["a", "z"]
    assert routes[0].source_id == "plugin:a"


def test_plugin_command_cannot_override_builtin_or_peer() -> None:
    with pytest.raises(ValueError, match="built-in"):
        build_command_catalog(
            (("plugin:a", PluginCommand("help", "skill:x")),), reserved=frozenset({"help"})
        )
    with pytest.raises(ValueError, match="duplicate"):
        build_command_catalog(
            (
                ("plugin:a", PluginCommand("review", "skill:a")),
                ("plugin:b", PluginCommand("review", "skill:b")),
            )
        )


def test_all_declarative_command_target_kinds_are_preserved() -> None:
    routes = build_command_catalog(
        (
            ("plugin:a", PluginCommand("skill", "skill:review")),
            ("plugin:a", PluginCommand("prompt", "prompt:review")),
            ("plugin:a", PluginCommand("capability", "capability:lint")),
        )
    )

    assert [route.target for route in routes] == [
        "capability:lint",
        "prompt:review",
        "skill:review",
    ]
