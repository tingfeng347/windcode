from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Add the prebuilt native helper only to a platform-specific Windows wheel."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        helper_value = os.environ.get("WINDCODE_WINDOWS_HELPER")
        wheel_tag = os.environ.get("WINDCODE_WINDOWS_WHEEL_TAG")
        if not helper_value and not wheel_tag:
            return
        if not helper_value or not wheel_tag:
            msg = "WINDCODE_WINDOWS_HELPER and WINDCODE_WINDOWS_WHEEL_TAG must be set together"
            raise RuntimeError(msg)
        helper = Path(helper_value).resolve()
        if not helper.is_file():
            raise RuntimeError(f"Windows sandbox helper does not exist: {helper}")
        if not wheel_tag.startswith("py3-none-win_"):
            raise RuntimeError(f"invalid Windows wheel tag: {wheel_tag}")
        build_data["pure_python"] = False
        build_data["tag"] = wheel_tag
        build_data["force_include"][str(helper)] = "windcode/sandbox/bin/windcode-sandbox.exe"
