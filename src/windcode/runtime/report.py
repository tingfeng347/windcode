from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from windcode.domain.events import RunResult
from windcode.domain.models import Usage
from windcode.domain.tools import ToolResult

_TEST_COMMAND = re.compile(r"(?:^|\s)(?:pytest|pyright|ruff|npm\s+test|pnpm\s+test|cargo\s+test)\b")


@dataclass(frozen=True, slots=True)
class ToolExecutionRecord:
    tool_name: str
    arguments: Mapping[str, Any]
    result: ToolResult


def build_run_result(
    final_text: str,
    records: tuple[ToolExecutionRecord, ...],
    *,
    usage: Usage | None = None,
) -> RunResult:
    changed_files: list[str] = []
    verification: list[str] = []
    failed_verification = False
    for record in records:
        path = record.result.data.get("path")
        action = record.result.data.get("action")
        if isinstance(path, str) and isinstance(action, str) and path not in changed_files:
            changed_files.append(path)
        changes = record.result.data.get("changes")
        if isinstance(changes, list):
            raw_changes = cast(list[object], changes)
            for change in raw_changes:
                if isinstance(change, Mapping):
                    raw_change = cast(Mapping[object, object], change)
                    changed_path = raw_change.get("path")
                    if isinstance(changed_path, str) and changed_path not in changed_files:
                        changed_files.append(changed_path)
        if record.tool_name == "shell":
            command = record.arguments.get("command")
            exit_code = record.result.data.get("exit_code")
            if isinstance(command, str) and _TEST_COMMAND.search(command):
                verification.append(f"{command} (exit {exit_code})")
                failed_verification = failed_verification or exit_code != 0

    if failed_verification:
        status = "failed"
    elif not verification:
        status = "unverified"
    else:
        status = "completed"
    return RunResult(
        status=status,
        final_text=final_text,
        changed_files=tuple(changed_files),
        verification=tuple(verification),
        usage=usage or Usage(),
    )
