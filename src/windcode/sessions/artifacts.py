from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

from windcode.sessions.models import ArtifactReference


class ArtifactStore:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir.expanduser().resolve()
        self.artifacts_dir = self.session_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def put(self, content: str, *, preview_chars: int = 240) -> ArtifactReference:
        encoded = content.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        relative_path = f"artifacts/{digest}.txt"
        destination = self.session_dir / relative_path
        if not destination.exists():
            temporary = destination.with_suffix(f".tmp-{uuid4().hex}")
            try:
                with temporary.open("wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    temporary.replace(destination)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        preview = content[:preview_chars]
        if len(content) > preview_chars:
            preview += "..."
        return ArtifactReference(
            relative_path=relative_path,
            sha256=digest,
            content_length=len(content),
            preview=preview,
        )

    def externalize(
        self,
        content: str,
        *,
        threshold: int,
    ) -> tuple[str, ArtifactReference | None]:
        if len(content) <= threshold:
            return content, None
        reference = self.put(content)
        summary = (
            f"{reference.preview}\n\n"
            f"[full output: {reference.relative_path}; sha256={reference.sha256}; "
            f"characters={reference.content_length}]"
        )
        return summary, reference

    def read(self, reference: ArtifactReference) -> str:
        path = (self.session_dir / reference.relative_path).resolve()
        if not path.is_relative_to(self.artifacts_dir):
            raise ValueError("artifact reference escapes the artifact directory")
        content = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest != reference.sha256:
            raise ValueError("artifact digest mismatch")
        return content
