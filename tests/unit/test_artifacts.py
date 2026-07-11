from pathlib import Path

from windcode.sessions import ArtifactStore


def test_externalizes_large_content_and_deduplicates(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    store = ArtifactStore(session_dir)
    content = "large output\n" * 100

    summary, first = store.externalize(content, threshold=20)
    _, second = store.externalize(content, threshold=20)

    assert first is not None
    assert second == first
    assert first.relative_path in summary
    assert store.read(first) == content
    assert len(tuple((session_dir / "artifacts").iterdir())) == 1


def test_keeps_small_content_inline(tmp_path: Path) -> None:
    inline, reference = ArtifactStore(tmp_path / "session").externalize("short", threshold=20)

    assert inline == "short"
    assert reference is None
