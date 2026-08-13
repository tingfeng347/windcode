from pathlib import Path

import pytest

from windcode.extensions.plugins.installer import install_local_plugin


def _plugin(tmp_path: Path, body: str = "body") -> Path:
    root = tmp_path / "source"
    (root / ".windcode-plugin").mkdir(parents=True)
    (root / "skills" / "review").mkdir(parents=True)
    (root / ".windcode-plugin" / "plugin.toml").write_text(
        """manifest_version = 1
id = "example"
name = "Example"
version = "1.0.0"
windcode = ">=3.0,<4.0"
skills = [{ id = "review", path = "skills/review" }]
"""
    )
    (root / "skills" / "review" / "SKILL.md").write_text(body)
    return root


def test_install_is_atomic_content_addressed_and_idempotent(tmp_path: Path) -> None:
    source = _plugin(tmp_path)
    destination = tmp_path / "plugins"

    first = install_local_plugin(source, destination)
    second = install_local_plugin(source, destination)

    assert first.changed
    assert not second.changed
    assert first.destination == second.destination
    assert first.destination.name == first.digest
    assert not list((destination / "example").glob(".tmp-*"))


def test_same_version_different_content_is_rejected(tmp_path: Path) -> None:
    source = _plugin(tmp_path)
    destination = tmp_path / "plugins"
    install_local_plugin(source, destination)
    (source / "skills" / "review" / "SKILL.md").write_text("changed")

    with pytest.raises(ValueError, match="different content"):
        install_local_plugin(source, destination)


def test_install_does_not_follow_symlinks(tmp_path: Path) -> None:
    source = _plugin(tmp_path)
    (source / "secret").symlink_to(tmp_path / "outside")

    result = install_local_plugin(source, tmp_path / "plugins")

    assert not (result.destination / "secret").exists()
