from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

INSTRUCTION_FILES = ("AGENTS.md", "WINDCODE.md", "CLAUDE.md", "HERMES.md")


@dataclass(frozen=True, slots=True)
class InstructionBlock:
    path: Path
    content: str


def find_workspace_root(current_dir: Path, workspace_root: Path | None = None) -> Path:
    current = current_dir.expanduser().resolve()
    if workspace_root is not None:
        root = workspace_root.expanduser().resolve()
        if not current.is_relative_to(root):
            raise ValueError(f"current directory {current} is outside workspace {root}")
        return root

    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def load_instructions(
    current_dir: Path,
    *,
    workspace_root: Path | None = None,
) -> tuple[InstructionBlock, ...]:
    current = current_dir.expanduser().resolve()
    root = find_workspace_root(current, workspace_root)
    relative = current.relative_to(root)
    directories = [root]
    cursor = root
    for part in relative.parts:
        cursor /= part
        directories.append(cursor)

    blocks: list[InstructionBlock] = []
    for directory in directories:
        for filename in INSTRUCTION_FILES:
            path = directory / filename
            if path.is_file():
                blocks.append(InstructionBlock(path=path, content=path.read_text(encoding="utf-8")))
                break
    return tuple(blocks)
