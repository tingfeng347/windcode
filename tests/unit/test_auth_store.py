from __future__ import annotations

import json
import os
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
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_malformed_auth_file_does_not_expose_contents(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text('{"secret": "sensitive"', encoding="utf-8")

    with pytest.raises(CredentialStoreError) as captured:
        FileCredentialStore(path).get("secret")

    assert "sensitive" not in str(captured.value)


def test_default_store_reads_legacy_credentials_then_writes_unified_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unified_root = tmp_path / ".windcode"
    legacy_root = tmp_path / "legacy-data" / "windcode"
    legacy_root.mkdir(parents=True)
    (legacy_root / "auth.json").write_text(
        '{"openai": {"type": "api", "key": "old-secret"}}', encoding="utf-8"
    )
    monkeypatch.setattr("windcode.auth.store.default_user_storage_root", lambda: unified_root)

    def legacy_data_path(_name: str) -> Path:
        return legacy_root

    monkeypatch.setattr("windcode.auth.store.user_data_path", legacy_data_path)

    store = FileCredentialStore()

    assert store.path == unified_root / "auth.json"
    assert store.get("openai") == "old-secret"
    store.set("anthropic", "new-secret")
    assert json.loads(store.path.read_text(encoding="utf-8")) == {
        "anthropic": {"key": "new-secret", "type": "api"},
        "openai": {"key": "old-secret", "type": "api"},
    }
