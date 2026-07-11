from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.tools.filesystem import atomic_write_text, content_sha256, require_workspace_path

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?(?:\n)?$")


@dataclass(frozen=True, slots=True)
class HunkLine:
    operation: Literal[" ", "+", "-"]
    content: str


@dataclass(frozen=True, slots=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[HunkLine, ...]


@dataclass(frozen=True, slots=True)
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: tuple[Hunk, ...]


class PatchParseError(ValueError):
    pass


def _parse_path(header: str, prefix: str) -> str | None:
    if not header.startswith(prefix):
        raise PatchParseError(f"expected {prefix.strip()} header")
    raw = header[len(prefix) :].strip().split("\t", 1)[0]
    if raw == "/dev/null":
        return None
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise PatchParseError(f"unsafe patch path: {raw}")
    return str(path)


def parse_unified_diff(text: str) -> tuple[FilePatch, ...]:
    if "GIT binary patch" in text or "Binary files " in text:
        raise PatchParseError("binary patches are not supported")
    lines = text.splitlines(keepends=True)
    files: list[FilePatch] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            index += 1
            continue
        old_path = _parse_path(lines[index], "--- ")
        index += 1
        if index >= len(lines):
            raise PatchParseError("missing new file header")
        new_path = _parse_path(lines[index], "+++ ")
        index += 1
        if old_path is None and new_path is None:
            raise PatchParseError("both patch paths cannot be /dev/null")
        hunks: list[Hunk] = []
        while index < len(lines) and not lines[index].startswith("--- "):
            if lines[index].startswith(("diff --git ", "index ")):
                index += 1
                continue
            match = _HUNK_HEADER.match(lines[index])
            if match is None:
                if lines[index].strip():
                    raise PatchParseError(f"unexpected patch line: {lines[index].rstrip()}")
                index += 1
                continue
            old_start = int(match.group(1))
            old_count = int(match.group(2) or "1")
            new_start = int(match.group(3))
            new_count = int(match.group(4) or "1")
            index += 1
            hunk_lines: list[HunkLine] = []
            while index < len(lines):
                line = lines[index]
                if line.startswith(("@@ ", "--- ")):
                    break
                if line.startswith("\\ No newline at end of file"):
                    if not hunk_lines:
                        raise PatchParseError("orphan no-newline marker")
                    previous = hunk_lines[-1]
                    hunk_lines[-1] = HunkLine(previous.operation, previous.content.rstrip("\r\n"))
                    index += 1
                    continue
                if not line or line[0] not in {" ", "+", "-"}:
                    raise PatchParseError(f"invalid hunk line: {line.rstrip()}")
                hunk_lines.append(HunkLine(cast(Literal[" ", "+", "-"], line[0]), line[1:]))
                index += 1
            actual_old = sum(item.operation != "+" for item in hunk_lines)
            actual_new = sum(item.operation != "-" for item in hunk_lines)
            if (actual_old, actual_new) != (old_count, new_count):
                raise PatchParseError(
                    f"hunk count mismatch: expected {old_count}/{new_count}, "
                    f"found {actual_old}/{actual_new}"
                )
            hunks.append(Hunk(old_start, old_count, new_start, new_count, tuple(hunk_lines)))
        if not hunks:
            raise PatchParseError("file patch has no hunks")
        files.append(FilePatch(old_path, new_path, tuple(hunks)))
    if not files:
        raise PatchParseError("no unified diff file headers found")
    return tuple(files)


def apply_file_patch(content: str, patch: FilePatch) -> str:
    source = content.splitlines(keepends=True)
    output: list[str] = []
    cursor = 0
    for hunk in patch.hunks:
        target = max(hunk.old_start - 1, 0)
        if target < cursor or target > len(source):
            raise ValueError("hunks overlap or start beyond the source file")
        output.extend(source[cursor:target])
        cursor = target
        for line in hunk.lines:
            if line.operation in {" ", "-"}:
                if cursor >= len(source) or source[cursor] != line.content:
                    raise ValueError(f"patch context does not match source at line {cursor + 1}")
                if line.operation == " ":
                    output.append(source[cursor])
                cursor += 1
            else:
                output.append(line.content)
    output.extend(source[cursor:])
    return "".join(output)


class ApplyPatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch: str = Field(min_length=1)
    expected_sha256: dict[str, str] = Field(default_factory=dict[str, str])


class ApplyPatchTool:
    name = "apply_patch"
    description = "Preflight and atomically apply a bounded multi-file unified diff."
    input_model = ApplyPatchInput
    effects = frozenset({ToolEffect.WORKSPACE_WRITE})

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        parsed = cast(ApplyPatchInput, arguments)
        patches = parse_unified_diff(parsed.patch)
        planned: list[tuple[FilePatch, Path, str | None, str]] = []
        for patch in patches:
            relative = patch.new_path or patch.old_path
            if relative is None:
                raise PatchParseError("patch is missing a usable path")
            path = require_workspace_path(context.workspace, relative)
            old_content = "" if patch.old_path is None else path.read_text(encoding="utf-8")
            expected = parsed.expected_sha256.get(relative)
            actual = content_sha256(old_content) if patch.old_path is not None else None
            if expected is not None and expected != actual:
                return ToolResult(
                    output=f"{relative}: file changed since patch was prepared",
                    is_error=True,
                    data={"error": "stale_content", "path": relative, "actual_sha256": actual},
                )
            new_content = apply_file_patch(old_content, patch)
            planned.append((patch, path, None if patch.new_path is None else new_content, relative))

        changes: list[dict[str, object]] = []
        for patch, path, new_content, relative in planned:
            if new_content is None:
                path.unlink()
                action = "deleted"
            else:
                atomic_write_text(path, new_content)
                action = "created" if patch.old_path is None else "modified"
            changes.append({"path": relative, "action": action})
        return ToolResult(
            output=f"applied patch to {len(changes)} file(s)",
            data={"changes": changes},
        )
