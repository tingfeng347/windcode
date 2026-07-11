import pytest

from windcode.tui.commands import COMMAND_CATALOG, COMMANDS, complete_commands, parse_command


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_parses_all_supported_commands(name: str) -> None:
    command = parse_command(f"/{name} argument")
    assert command.name == name
    assert command.arguments == ("argument",)


def test_rejects_unknown_command() -> None:
    with pytest.raises(ValueError, match="未知命令"):
        parse_command("/unknown")


def test_command_catalog_matches_parser_commands() -> None:
    assert {command.name for command in COMMAND_CATALOG} == COMMANDS
    assert all(command.description for command in COMMAND_CATALOG)


def test_filters_commands_by_slash_prefix() -> None:
    assert [command.name for command in complete_commands("/")] == [
        command.name for command in COMMAND_CATALOG
    ]
    assert [command.name for command in complete_commands("/MO")] == ["mode", "model"]
    assert complete_commands("/missing") == ()


@pytest.mark.parametrize("value", ["model", "/model name", "/model\n"])
def test_does_not_complete_non_prefix_input(value: str) -> None:
    assert complete_commands(value) == ()
