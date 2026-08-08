from pathlib import Path

import pytest

from windcode.extensions.models import (
    CapabilityKind,
    CapabilityRecord,
    ExtensionScope,
    ExtensionSource,
)
from windcode.extensions.skills.loader import SkillLoader
from windcode.extensions.skills.parser import SkillMetadata, parse_skill_metadata


def _skill(tmp_path: Path) -> tuple[CapabilityRecord, SkillMetadata]:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: review\ndescription: Review\n---\nbody")
    (root / "reference.txt").write_text("reference")
    record = CapabilityRecord(
        "skill:review",
        "review",
        CapabilityKind.SKILL,
        ExtensionSource(ExtensionScope.USER, root),
    )
    return record, parse_skill_metadata(root)


def test_content_and_references_are_loaded_on_demand_and_cached(tmp_path: Path) -> None:
    record, metadata = _skill(tmp_path)
    loader = SkillLoader(max_content_bytes=1024)
    content = loader.load(record, metadata)
    (metadata.root / "SKILL.md").write_text("changed")

    assert loader.load(record, metadata) is content
    reference = loader.read_reference(record, metadata, content, "reference.txt")
    assert reference.content == b"reference"


def test_reference_cannot_escape_skill_root(tmp_path: Path) -> None:
    record, metadata = _skill(tmp_path)
    loader = SkillLoader(max_content_bytes=1024)
    content = loader.load(record, metadata)
    with pytest.raises(ValueError, match="escapes"):
        loader.read_reference(record, metadata, content, "../outside")
