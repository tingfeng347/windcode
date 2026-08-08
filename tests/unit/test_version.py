import tomllib
from importlib.metadata import version
from pathlib import Path
from typing import cast

import windcode
from windcode.version import VERSION


def test_public_version_matches_package_metadata() -> None:
    project = cast(
        dict[str, object], tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    )
    metadata = cast(dict[str, object], project["project"])

    assert VERSION == windcode.__version__ == version("windcode") == metadata["version"]
