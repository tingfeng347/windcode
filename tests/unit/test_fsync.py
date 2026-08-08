from pathlib import Path

import pytest

import windcode._fsync as fsync_module


def test_directory_fsync_is_skipped_when_platform_does_not_support_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(fsync_module, "_DIRECTORY_FSYNC_SUPPORTED", False)

    def fail_open(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise AssertionError("os.open must not be called")

    monkeypatch.setattr(fsync_module.os, "open", fail_open)

    fsync_module.fsync_directory(tmp_path)
