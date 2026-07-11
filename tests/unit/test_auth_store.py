from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from windcode.auth import CredentialStoreError, FileCredentialStore


def test_file_store_survives_new_instance_with_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "credentials" / "auth.json"
    FileCredentialStore(path).set("openai", "secret-value")

    assert FileCredentialStore(path).get("openai") == "secret-value"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "openai": {"key": "secret-value", "type": "api"}
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_malformed_auth_file_does_not_expose_contents(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text('{"secret": "sensitive"', encoding="utf-8")

    with pytest.raises(CredentialStoreError) as captured:
        FileCredentialStore(path).get("secret")

    assert "sensitive" not in str(captured.value)
