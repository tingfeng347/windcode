from __future__ import annotations

from dataclasses import dataclass

from windcode.extensions.plugins.manifest import PluginCommand


@dataclass(frozen=True, slots=True)
class CommandRoute:
    name: str
    target: str
    source_id: str


def build_command_catalog(
    commands: tuple[tuple[str, PluginCommand], ...], *, reserved: frozenset[str] = frozenset()
) -> tuple[CommandRoute, ...]:
    catalog: dict[str, CommandRoute] = {}
    for source_id, command in sorted(commands, key=lambda item: (item[1].name, item[0])):
        if command.name in reserved:
            raise ValueError(f"plugin command conflicts with built-in command: {command.name}")
        if command.name in catalog:
            raise ValueError(f"duplicate plugin command: {command.name}")
        catalog[command.name] = CommandRoute(command.name, command.target, source_id)
    return tuple(catalog.values())
