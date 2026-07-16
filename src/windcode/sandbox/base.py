from __future__ import annotations

from pathlib import Path
from typing import Protocol

from windcode.sandbox.models import LaunchSpec, SandboxPolicy, SandboxStatus


class SandboxBackend(Protocol):
    @property
    def status(self) -> SandboxStatus: ...

    def prepare(
        self, command: tuple[str, ...], *, cwd: Path, policy: SandboxPolicy
    ) -> LaunchSpec: ...

    def classifies_denial(self, returncode: int | None, stderr: str) -> bool: ...
