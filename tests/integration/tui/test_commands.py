from pathlib import Path

import pytest

from windcode.memory import MemoryKind, MemoryScope
from windcode.sdk import Windcode
from windcode.tui.command_handlers import MemoryCommandHandler
from windcode.tui.commands import COMMAND_CATALOG, COMMANDS, complete_commands, parse_command


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_parses_all_supported_commands(name: str) -> None:
    command = parse_command(f"/{name} argument")
    assert command.name == name
    assert command.arguments == ("argument",)


def test_rejects_unknown_command() -> None:
    with pytest.raises(ValueError, match="未知命令"):
        parse_command("/unknown")


@pytest.mark.parametrize("name", ["history", "mode"])
def test_rejects_removed_commands(name: str) -> None:
    with pytest.raises(ValueError, match="未知命令"):
        parse_command(f"/{name}")


def test_command_catalog_matches_parser_commands() -> None:
    assert {command.name for command in COMMAND_CATALOG} == COMMANDS
    assert all(command.description for command in COMMAND_CATALOG)


def test_filters_commands_by_slash_prefix() -> None:
    assert [command.name for command in complete_commands("/")] == [
        command.name for command in COMMAND_CATALOG
    ]
    assert [command.name for command in complete_commands("/MO")] == ["model"]
    assert complete_commands("/missing") == ()


@pytest.mark.parametrize("value", ["model", "/model name", "/model\n"])
def test_does_not_complete_non_prefix_input(value: str) -> None:
    assert complete_commands(value) == ()


def test_disabled_memory_command_reports_status_and_rejects_queries(tmp_path: Path) -> None:
    handler = MemoryCommandHandler(Windcode.open(state_root=tmp_path / "state"))

    assert handler.execute(("status",), enabled=False).message == "长期记忆: 已禁用"
    with pytest.raises(ValueError, match="已在配置中禁用"):
        handler.execute(("search", "query"), enabled=False)


@pytest.mark.asyncio
async def test_memory_commands_share_sdk_state_and_prefix_rules(tmp_path: Path) -> None:
    client = Windcode.open(
        {"memory": {"enabled": True}},
        state_root=tmp_path / "state",
        workspace=tmp_path,
    )
    async with client:
        memory = client.create_memory_candidate(
            kind=MemoryKind.SOP,
            scope=MemoryScope.PROJECT,
            title="Release checks",
            summary="Run checks",
            body="Run focused checks before release.",
        )
        handler = MemoryCommandHandler(client)

        assert "候选 1" in str(handler.execute(("status",), enabled=True).message)
        assert "Release checks" in str(
            handler.execute(("show", memory.memory_id[:10]), enabled=True).message
        )
        confirmed = handler.execute(("confirm", memory.memory_id[:10]), enabled=True)
        assert confirmed.message == "记忆已确认: Release checks"
        activated = handler.execute(("activation", memory.memory_id[:10], "always"), enabled=True)
        assert activated.message == "记忆激活策略已更新: Release checks -> always"
