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


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    name: str
    description: str
    argument_hint: str = ""

    @property
    def value(self) -> str:
        return f"${self.name}"


CompletionDefinition = CommandDefinition | SkillDefinition


COMMAND_CATALOG = (
    CommandDefinition("new", "新建会话"),
    CommandDefinition("resume", "恢复已有会话", "[会话 ID 或短 ID]"),
    CommandDefinition("history", "显示当前会话历史节点"),
    CommandDefinition("rewind", "回退到历史记录", "<记录 ID 或短 ID>"),
    CommandDefinition("mode", "切换权限模式", "<模式>"),
    CommandDefinition("model", "配置或切换当前模型", "[配置别名]"),
    CommandDefinition("compact", "压缩当前上下文"),
    CommandDefinition("clear", "清空当前消息显示"),
    CommandDefinition("status", "显示会话状态"),
    CommandDefinition("agents", "显示子智能体状态"),
    CommandDefinition(
        "extensions",
        "管理扩展",
        "[list|inspect|install|enable|disable|reload|trust] [目标]",
    ),
    CommandDefinition(
        "memory",
        "管理长期记忆",
    ),
    CommandDefinition("help", "显示可用命令"),
    CommandDefinition("quit", "退出 Windcode"),
)

COMMANDS = frozenset(command.name for command in COMMAND_CATALOG)


@dataclass(frozen=True, slots=True)
class SlashCommand:
    name: str
    arguments: tuple[str, ...] = ()


def complete_commands(
    prefix: str, extra: tuple[CommandDefinition, ...] = ()
) -> tuple[CommandDefinition, ...]:
    if prefix != prefix.strip() or not prefix.startswith("/") or " " in prefix or "\n" in prefix:
        return ()
    name_prefix = prefix[1:].casefold()
    catalog = (*COMMAND_CATALOG, *extra)
    return tuple(command for command in catalog if command.name.startswith(name_prefix))


def complete_skills(
    prefix: str, skills: tuple[SkillDefinition, ...]
) -> tuple[SkillDefinition, ...]:
    if prefix != prefix.strip() or not prefix.startswith("$") or " " in prefix or "\n" in prefix:
        return ()
    name_prefix = prefix[1:].casefold()
    return tuple(skill for skill in skills if skill.name.casefold().startswith(name_prefix))


def parse_command(value: str, extra_names: frozenset[str] = frozenset()) -> SlashCommand:
    parts = value.strip().split()
    if not parts or not parts[0].startswith("/"):
        raise ValueError("命令必须以 / 开头")
    name = parts[0][1:].casefold()
    if name not in COMMANDS and name not in extra_names:
        raise ValueError(f"未知命令: /{name}")
    return SlashCommand(name, tuple(parts[1:]))
