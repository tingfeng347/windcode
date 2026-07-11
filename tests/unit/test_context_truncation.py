from pathlib import Path

from windcode.context import truncate_context
from windcode.domain.messages import (
    AttachmentBlock,
    Message,
    Role,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from windcode.sessions import ArtifactStore


def test_removes_old_media_and_externalizes_old_large_tool_results(tmp_path: Path) -> None:
    old_result = ToolResultBlock("done", "read", "x" * 200)
    messages = (
        Message(Role.USER, (TextBlock("old"), AttachmentBlock("image/png", "image"))),
        Message(Role.TOOL, (old_result,)),
        Message(Role.USER, (TextBlock("recent"),)),
    )

    result = truncate_context(
        messages,
        ArtifactStore(tmp_path / "session"),
        max_tool_result_chars=100,
        preserve_recent_turns=1,
    )

    assert result.changed
    assert result.removed_attachments == 1
    assert len(result.artifacts) == 1
    transformed = result.messages[1].content[0]
    assert isinstance(transformed, ToolResultBlock)
    assert transformed.artifact_ref == result.artifacts[0].relative_path
    assert messages[0].content[1] == AttachmentBlock("image/png", "image")
    assert old_result.artifact_ref is None


def test_preserves_system_recent_and_open_tool_call(tmp_path: Path) -> None:
    messages = (
        Message(Role.SYSTEM, (AttachmentBlock("text/plain", "system-asset"),)),
        Message(Role.ASSISTANT, (ToolCallBlock("open", "shell", {}),)),
        Message(Role.USER, (TextBlock("recent"), AttachmentBlock("image/png", "recent"))),
    )

    result = truncate_context(
        messages,
        ArtifactStore(tmp_path / "session"),
        preserve_recent_turns=1,
    )

    assert result.messages == messages
    assert not result.changed
