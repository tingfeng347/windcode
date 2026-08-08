from pathlib import Path

import pytest

from windcode.auth import FileCredentialStore
from windcode.config.models import AppConfig, McpStdioConfig
from windcode.extensions.hooks.models import CommandAction, HookDefinition, HookEvent, HookMatcher
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


def test_effective_plugin_required_flag_reaches_mcp_runtime(tmp_path: Path) -> None:
    definition = McpStdioConfig(command="never-started", required=False)
    record = CapabilityRecord(
        "plugin:required/mcp_server/server",
        "server",
        CapabilityKind.MCP_SERVER,
        ExtensionSource(
            ExtensionScope.USER,
            tmp_path,
            plugin_id="required",
            component_id="server",
        ),
        required=True,
    )
    snapshot = ExtensionSnapshot(
        1,
        "required-plugin",
        (record,),
        {record.capability_id: definition},
    )

    runtime = RunExtensions.create(
        snapshot,
        session_id="session",
        run_id="run",
        credential_store=FileCredentialStore(tmp_path / "credentials.json"),
        max_content_bytes=1024,
        connect_timeout=0.1,
        call_timeout=0.1,
    )

    assert runtime.mcp.required_server_ids == ("server",)


@pytest.mark.asyncio
async def test_effective_plugin_required_flag_makes_hook_failure_fatal(tmp_path: Path) -> None:
    definition = HookDefinition(
        "startup",
        "plugin:required/startup",
        HookMatcher(HookEvent.RUN_START),
        CommandAction("never-run"),
        required=False,
    )
    record = CapabilityRecord(
        "plugin:required/hook/startup",
        "startup",
        CapabilityKind.HOOK,
        ExtensionSource(
            ExtensionScope.USER,
            tmp_path,
            plugin_id="required",
            component_id="startup",
        ),
        required=True,
    )
    snapshot = ExtensionSnapshot(1, "required-hook", (record,), {record.capability_id: definition})
    runtime = RunExtensions.create(
        snapshot,
        session_id="session",
        run_id="run",
        credential_store=FileCredentialStore(tmp_path / "credentials.json"),
        max_content_bytes=1024,
        connect_timeout=0.1,
        call_timeout=0.1,
    )

    with pytest.raises(RuntimeError, match="required Hook failed"):
        await runtime.lifecycle(HookEvent.RUN_START)
