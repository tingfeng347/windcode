from __future__ import annotations

import base64
import json
import re
import shlex
import shutil
import subprocess
from enum import StrEnum
from time import monotonic
from typing import Any, cast

from pydantic import BaseModel, ConfigDict


class ShellDialect(StrEnum):
    BASH = "bash"
    POWERSHELL = "powershell"


class CommandAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    argv: tuple[str, ...]
    source: str
    redirects: tuple[str, ...] = ()
    command_substitution: bool = False
    critical: bool = False


class CommandAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dialect: ShellDialect
    actions: tuple[CommandAction, ...]
    trusted: bool
    error: str | None = None

    @property
    def critical(self) -> bool:
        return any(action.critical for action in self.actions)


class CommandRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = "shell"
    dialect: ShellDialect
    argv_prefix: tuple[str, ...]
    exact: bool = False
    network: bool = False
    escalated: bool = False
    source: str = "project"

    def matches(
        self,
        action: CommandAction,
        *,
        dialect: ShellDialect,
        network: bool,
        escalated: bool = False,
    ) -> bool:
        if (
            dialect is not self.dialect
            or network != self.network
            or escalated != self.escalated
            or action.critical
        ):
            return False
        if self.exact:
            return action.argv == self.argv_prefix
        return action.argv[: len(self.argv_prefix)] == self.argv_prefix


_BASH_CRITICAL = {
    "mkfs",
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
}
_POWERSHELL_CRITICAL = {
    "clear-disk",
    "format-volume",
    "restart-computer",
    "stop-computer",
}
_STABLE_ARITY = {
    "git": 2,
    "gh": 2,
    "docker": 2,
    "kubectl": 2,
    "npm": 2,
    "pnpm": 2,
    "yarn": 2,
    "uv": 2,
    "python": 1,
    "python3": 1,
    "pytest": 1,
    "ruff": 2,
    "cargo": 2,
}
_REDIRECTION = re.compile(r"(?:^|\s)(?:\d*>>?|<<?)\s*([^\s;&|]+)")


def _is_critical(argv: tuple[str, ...], dialect: ShellDialect) -> bool:
    if not argv:
        return False
    executable = argv[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1].casefold()
    if dialect is ShellDialect.POWERSHELL:
        if executable in _POWERSHELL_CRITICAL:
            return True
        return executable in {"remove-item", "del", "erase"} and {
            "-recurse",
            "-force",
        } <= {item.casefold() for item in argv[1:]}
    if executable in _BASH_CRITICAL:
        return True
    if executable == "dd":
        return any(item.startswith("of=/dev/") for item in argv[1:])
    if executable == "rm":
        flags = "".join(
            item[1:] for item in argv[1:] if item.startswith("-") and not item.startswith("--")
        )
        recursive = "r" in flags or "R" in flags or "--recursive" in argv
        forced = "f" in flags or "--force" in argv
        targets = tuple(item for item in argv[1:] if not item.startswith("-"))
        return recursive and forced and bool(targets)
    return False


def _action(source: str, dialect: ShellDialect, argv: list[str]) -> CommandAction:
    normalized = tuple(item for item in argv if item)
    redirects = tuple(match.group(1) for match in _REDIRECTION.finditer(source))
    return CommandAction(
        argv=normalized,
        source=source.strip(),
        redirects=redirects,
        command_substitution="$(" in source or "`" in source or "$(" in source,
        critical=_is_critical(normalized, dialect),
    )


def _bash_tree_actions(
    command: str, *, max_nodes: int = 50_000, max_seconds: float = 0.05
) -> tuple[CommandAction, ...]:
    import tree_sitter_bash  # type: ignore[import-untyped]
    from tree_sitter import Language, Parser  # type: ignore[import-untyped]

    parser = Parser(Language(tree_sitter_bash.language()))
    started = monotonic()
    tree = parser.parse(command.encode())
    if monotonic() - started > max_seconds:
        raise ValueError("bash parser time limit exceeded")
    if tree.root_node.has_error:
        raise ValueError("bash syntax tree contains errors")
    nodes: list[Any] = []
    node_count = 0

    def visit(node: Any) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > max_nodes:
            raise ValueError("bash parser node limit exceeded")
        if node.type == "command":
            nodes.append(node)
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    actions: list[CommandAction] = []
    raw = command.encode()
    for node in nodes:
        command_source = raw[node.start_byte : node.end_byte].decode(errors="replace")
        parent = node.parent
        action_source = (
            raw[parent.start_byte : parent.end_byte].decode(errors="replace")
            if parent is not None and parent.type == "redirected_statement"
            else command_source
        )
        argv = shlex.split(command_source, posix=True)
        if argv:
            actions.append(_action(action_source, ShellDialect.BASH, argv))
    return tuple(actions)


def analyze_bash(command: str, *, max_bytes: int = 128_000) -> CommandAnalysis:
    if len(command.encode()) > max_bytes:
        return CommandAnalysis(
            dialect=ShellDialect.BASH,
            actions=(),
            trusted=False,
            error="command too large",
        )
    try:
        actions = _bash_tree_actions(command)
    except (ImportError, ValueError, OSError) as exc:
        return CommandAnalysis(
            dialect=ShellDialect.BASH,
            actions=(),
            trusted=False,
            error=str(exc),
        )
    return CommandAnalysis(
        dialect=ShellDialect.BASH,
        actions=actions,
        trusted=bool(actions),
        error=None if actions else "no executable command found",
    )


_POWERSHELL_PARSER = (
    r"""
$ErrorActionPreference='Stop'
$src=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($args[0]))
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($src,[ref]$tokens,[ref]$errors)
if ($errors.Count -gt 0) { throw 'PowerShell parse error' }
$items=@()
"""
    + r"""$ast.FindAll({param($n) $n -is """
    + r"""[Management.Automation.Language.CommandAst]},$true) | ForEach-Object {
  $argv=@($_.CommandElements | ForEach-Object { $_.Extent.Text })
  $items += ,@{source=$_.Extent.Text; argv=$argv}
}
$items | ConvertTo-Json -Compress -Depth 4
"""
)


def analyze_powershell(command: str, *, executable: str | None = None) -> CommandAnalysis:
    shell = executable or shutil.which("pwsh") or shutil.which("powershell.exe")
    if shell is None:
        return CommandAnalysis(
            dialect=ShellDialect.POWERSHELL,
            actions=(),
            trusted=False,
            error="PowerShell parser unavailable",
        )
    encoded = base64.b64encode(command.encode("utf-16-le")).decode()
    try:
        result = subprocess.run(
            (shell, "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL_PARSER, encoded),
            capture_output=True,
            text=True,
            timeout=1.0,
            check=True,
        )
        decoded = cast(object, json.loads(result.stdout or "[]"))
        if isinstance(decoded, dict):
            rows: list[object] = [cast(dict[object, object], decoded)]
        elif isinstance(decoded, list):
            rows = cast(list[object], decoded)
        else:
            raise TypeError("PowerShell parser returned a non-list result")
        parsed_actions: list[CommandAction] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = cast(dict[object, object], row)
            raw_argv = item.get("argv")
            if not isinstance(raw_argv, list):
                continue
            parsed_actions.append(
                _action(
                    str(item["source"]),
                    ShellDialect.POWERSHELL,
                    [str(value) for value in cast(list[object], raw_argv)],
                )
            )
        actions = tuple(parsed_actions)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return CommandAnalysis(
            dialect=ShellDialect.POWERSHELL,
            actions=(),
            trusted=False,
            error=str(exc),
        )
    return CommandAnalysis(
        dialect=ShellDialect.POWERSHELL,
        actions=actions,
        trusted=bool(actions),
        error=None if actions else "no executable command found",
    )


def propose_rule(
    analysis: CommandAnalysis,
    *,
    network: bool,
    source: str,
    escalated: bool = False,
) -> CommandRule | None:
    if not analysis.trusted or len(analysis.actions) != 1 or analysis.critical:
        return None
    argv = analysis.actions[0].argv
    if not argv:
        return None
    executable = argv[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1].casefold()
    arity = _STABLE_ARITY.get(executable)
    if arity is None or len(argv) < arity:
        return CommandRule(
            dialect=analysis.dialect,
            argv_prefix=argv,
            exact=True,
            network=network,
            escalated=escalated,
            source=source,
        )
    return CommandRule(
        dialect=analysis.dialect,
        argv_prefix=argv[:arity],
        network=network,
        escalated=escalated,
        source=source,
    )
