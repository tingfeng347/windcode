from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from windcode.extensions.models import normalize_id
from windcode.extensions.paths import resolve_beneath


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    name: str
    description: str
    root: Path
    entrypoint: Path


def _read_frontmatter(path: Path, max_bytes: int) -> bytes:
    consumed = 0
    lines: list[bytes] = []
    with path.open("rb") as stream:
        first = stream.readline(max_bytes + 1)
        consumed += len(first)
        if first.rstrip(b"\r\n") != b"---":
            raise ValueError("SKILL.md must start with YAML frontmatter")
        while consumed <= max_bytes:
            line = stream.readline(max_bytes - consumed + 1)
            consumed += len(line)
            if not line:
                raise ValueError("SKILL.md frontmatter is not terminated")
            if line.rstrip(b"\r\n") == b"---":
                return b"".join(lines)
            lines.append(line)
    raise ValueError(f"Skill frontmatter exceeds {max_bytes} bytes")


def parse_skill_metadata(root: Path, *, max_bytes: int = 65_536) -> SkillMetadata:
    root = root.expanduser().resolve(strict=True)
    entrypoint = resolve_beneath(root, "SKILL.md", require_file=True)
    try:
        decoded = _read_frontmatter(entrypoint, max_bytes).decode("utf-8")
        value = yaml.safe_load(decoded)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid Skill frontmatter: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Skill frontmatter must be a mapping")
    raw = cast(dict[str, Any], value)
    if set(raw) - {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}:
        raise ValueError("Skill frontmatter contains unknown fields")
    name = normalize_id(str(raw.get("name", "")))
    description = raw.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise ValueError("Skill description must contain 1-1024 characters")
    return SkillMetadata(name, description.strip(), root, entrypoint)
