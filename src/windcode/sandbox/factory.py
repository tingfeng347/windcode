from __future__ import annotations

import sys
from pathlib import Path

from windcode.sandbox.base import SandboxBackend
from windcode.sandbox.bwrap import BubblewrapSandbox
from windcode.sandbox.models import SandboxPolicy, SandboxPreset
from windcode.sandbox.seatbelt import SeatbeltSandbox


def create_sandbox_backend(
    workspace: Path,
    *,
    platform: str | None = None,
    preset: SandboxPreset = SandboxPreset.WORKSPACE_WRITE,
    writable_roots: tuple[Path, ...] = (),
    network_enabled: bool = False,
) -> tuple[SandboxBackend | None, SandboxPolicy]:
    selected = platform or sys.platform
    policy = SandboxPolicy(preset, writable_roots, network_enabled)
    if preset is SandboxPreset.DANGER_FULL_ACCESS:
        return None, policy
    if selected.startswith("linux"):
        return BubblewrapSandbox(workspace, writable_paths=writable_roots), policy
    if selected == "darwin":
        return SeatbeltSandbox(workspace), policy
    return None, policy
