from pathlib import Path

import pytest

from windcode import Windcode
from windcode.extensions import service as extension_service_module


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [{"extensions": {"enabled": False}}])
async def test_disabled_extensions_do_not_scan_or_create_runtime_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: dict[str, object]
) -> None:
    scans = 0

    def fail_if_scanned(*args: object, **kwargs: object) -> object:
        nonlocal scans
        del args, kwargs
        scans += 1
        raise AssertionError("disabled extension service scanned extension roots")

    monkeypatch.setattr(extension_service_module, "discover_skills", fail_if_scanned)
    async with Windcode.open(config, state_root=tmp_path / "state", workspace=tmp_path) as client:
        assert client.extension_snapshot.capabilities == ()
        assert client.extension_snapshot.definitions == {}
        assert client.extension_service is not None
        assert scans == 0

    assert scans == 0
    assert not (tmp_path / "state" / "extensions" / "state.json").exists()
