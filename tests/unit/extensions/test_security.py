from pathlib import Path

import pytest

from windcode.auth import FileCredentialStore
from windcode.config.models import AppConfig
from windcode.extensions.models import (
    CapabilityKind,
    CapabilityRecord,
    ExtensionScope,
    ExtensionSnapshot,
    ExtensionSource,
)
from windcode.extensions.runtime import RunExtensions
from windcode.observability import REDACTED, DynamicRedactor


def _snapshot(config: AppConfig) -> ExtensionSnapshot:
    definition = config.extensions.mcp_servers["remote"]
    record = CapabilityRecord(
        "mcp_server:remote",
        "remote",
        CapabilityKind.MCP_SERVER,
        ExtensionSource(ExtensionScope.USER),
    )
    return ExtensionSnapshot(1, "x", (record,), {record.capability_id: definition})


@pytest.mark.asyncio
async def test_http_secret_is_lazy_redacted_and_network_policy_is_an_upper_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "unique-extension-secret"
    monkeypatch.setenv("MCP_TOKEN", marker)
    config = AppConfig.model_validate(
        {
            "extensions": {
                "enabled": True,
                "mcp_servers": {
                    "remote": {
                        "transport": "streamable_http",
                        "url": "http://127.0.0.1:1/mcp",
                        "headers": {"Authorization": {"env": "MCP_TOKEN"}},
                    }
                },
            }
        }
    )
    redactor = DynamicRedactor()
    runtime = RunExtensions.create(
        _snapshot(config),
        session_id="s",
        run_id="r",
        credential_store=FileCredentialStore(tmp_path / "credentials.json"),
        max_content_bytes=1024,
        connect_timeout=0.1,
        call_timeout=0.1,
        observe_secret=redactor.register,
        network_enabled=False,
    )

    assert redactor.redact(marker) == marker
    with pytest.raises(PermissionError, match="network policy"):
        await runtime.mcp.activate("remote")
    assert redactor.redact(marker) == marker
    await runtime.aclose()


def test_dynamic_redactor_clears_run_scoped_values() -> None:
    redactor = DynamicRedactor()
    redactor.register("secret-value")
    assert redactor.redact("prefix secret-value suffix") == f"prefix {REDACTED} suffix"
    redactor.clear()
    assert redactor.redact("secret-value") == "secret-value"
