from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    name: str
    description: str
    argument_hint: str = ""

    @property
    def value(self) -> str:
        return f"/{self.name}"


COMMAND_CATALOG = (
    CommandDefinition("new", "新建会话"),
    CommandDefinition("resume", "恢复已有会话", "[会话 ID]"),
    CommandDefinition("rewind", "回退到历史记录", "<记录 ID>"),
    CommandDefinition("mode", "切换权限模式", "<模式>"),
    CommandDefinition("model", "切换当前模型", "<模型>"),
    CommandDefinition("compact", "压缩当前上下文"),
    CommandDefinition("status", "显示会话状态"),
    CommandDefinition("quit", "退出 Windcode"),
)

COMMANDS = frozenset(command.name for command in COMMAND_CATALOG)


@dataclass(frozen=True, slots=True)
class SlashCommand:
    name: str
    arguments: tuple[str, ...] = ()


def complete_commands(prefix: str) -> tuple[CommandDefinition, ...]:
    if prefix != prefix.strip() or not prefix.startswith("/") or " " in prefix or "\n" in prefix:
        return ()
    name_prefix = prefix[1:].casefold()
    return tuple(command for command in COMMAND_CATALOG if command.name.startswith(name_prefix))


def parse_command(value: str) -> SlashCommand:
    parts = value.strip().split()
    if not parts or not parts[0].startswith("/"):
        raise ValueError("命令必须以 / 开头")
    name = parts[0][1:].casefold()
    if name not in COMMANDS:
        raise ValueError(f"未知命令: /{name}")
    return SlashCommand(name, tuple(parts[1:]))
