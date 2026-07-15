from __future__ import annotations

import os
from pathlib import Path

_DIRECTORY_FSYNC_SUPPORTED = os.name != "nt"


def fsync_directory(path: Path) -> None:
    """Durably flush a directory where the platform exposes directory handles.

    Windows does not allow directories to be opened with ``os.open`` and raises
    ``PermissionError``. File contents are still flushed before replacement; the
    directory flush is an additional POSIX durability guarantee.
    """

    if not _DIRECTORY_FSYNC_SUPPORTED:
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
