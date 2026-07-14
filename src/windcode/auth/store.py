from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, TypedDict, cast
from uuid import uuid4

from platformdirs import user_data_path

from windcode.config.paths import default_user_storage_root


class CredentialStoreError(RuntimeError):
    """Raised when credentials cannot be persisted safely."""


class CredentialStore(Protocol):
    def get(self, credential_id: str) -> str | None: ...

    def set(self, credential_id: str, secret: str) -> None: ...

    def delete(self, credential_id: str) -> None: ...


class ApiCredential(TypedDict):
    type: str
    key: str


class FileCredentialStore:
    """OpenCode-style API credentials stored in the user's data directory."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or default_user_storage_root() / "auth.json").expanduser().resolve()
        self._fallback_path = (
            None
            if path is not None
            else (user_data_path("windcode") / "auth.json").expanduser().resolve()
        )

    def _read(self) -> dict[str, ApiCredential]:
        source = self.path
        if not source.exists() and self._fallback_path is not None:
            source = self._fallback_path
        try:
            raw: object = json.loads(source.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialStoreError("无法读取 Windcode 凭据文件") from exc
        if not isinstance(raw, dict):
            raise CredentialStoreError("Windcode 凭据文件格式无效")
        values = cast(dict[object, object], raw)
        credentials: dict[str, ApiCredential] = {}
        for credential_id, value in values.items():
            if not isinstance(credential_id, str):
                continue
            if not isinstance(value, dict):
                continue
            record = cast(dict[object, object], value)
            credential_type = record.get("type")
            key = record.get("key")
            if credential_type == "api" and isinstance(key, str):
                credentials[credential_id] = {"type": "api", "key": key}
        return credentials

    def get(self, credential_id: str) -> str | None:
        credential = self._read().get(credential_id)
        return credential["key"] if credential is not None else None

    def set(self, credential_id: str, secret: str) -> None:
        values = self._read()
        values[credential_id] = {"type": "api", "key": secret}
        self._write(values)

    def delete(self, credential_id: str) -> None:
        values = self._read()
        if values.pop(credential_id, None) is not None:
            self._write(values)

    def _write(self, values: Mapping[str, ApiCredential]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            temporary = self.path.with_suffix(f"{self.path.suffix}.tmp-{uuid4().hex}")
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(dict(values), stream, ensure_ascii=True, indent=2, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.path)
                os.chmod(self.path, 0o600)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            raise CredentialStoreError("无法写入 Windcode 凭据文件") from exc
