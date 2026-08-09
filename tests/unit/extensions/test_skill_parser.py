from pathlib import Path

import pytest

from windcode.extensions.skills.parser import parse_skill_metadata


def test_parser_reads_frontmatter_without_reading_body(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_bytes(
        b"---\nname: review\ndescription: Review changes\n---\n" + b"x" * 100_000
    )

    metadata = parse_skill_metadata(root, max_bytes=1024)

    assert metadata.name == "review"
    assert metadata.description == "Review changes"


def test_parser_rejects_invalid_encoding_and_oversized_metadata(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_bytes(b"---\nname: x\ndescription: \xff\n---\n")
    with pytest.raises(ValueError, match="invalid Skill"):
        parse_skill_metadata(root)

    (root / "SKILL.md").write_text("---\nname: x\ndescription: " + "x" * 2000 + "\n---\n")
    with pytest.raises(ValueError, match="exceeds"):
        parse_skill_metadata(root, max_bytes=100)
