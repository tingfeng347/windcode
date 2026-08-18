from dataclasses import replace
from pathlib import Path

import pytest

from windcode.config import AppConfig, ProviderConfig, ProviderProtocol
from windcode.providers import (
    ProviderConfigurationError,
    ProviderDraft,
    ProviderService,
    ProviderStatus,
)


class MemoryCredentialStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, credential_id: str) -> str | None:
        return self.values.get(credential_id)

    def set(self, credential_id: str, secret: str) -> None:
        self.values[credential_id] = secret

    def delete(self, credential_id: str) -> None:
        self.values.pop(credential_id, None)


class FailingCredentialStore(MemoryCredentialStore):
    def get(self, credential_id: str) -> str | None:
        del credential_id
        raise RuntimeError("credential backend unavailable")


def provider_draft(alias: str, *, secret: str | None = None) -> ProviderDraft:
    return ProviderDraft(
        alias=alias,
        protocol=ProviderProtocol.OPENAI_RESPONSES,
        model="gpt-test",
        provider_id="openai",
        api_key_env="MISSING_TEST_KEY",
        credential_id=alias,
        base_url="https://api.openai.com/v1",
        secret=secret,
    )


@pytest.mark.asyncio
async def test_disconnected_provider_is_saved_but_not_promoted_to_default(tmp_path: Path) -> None:
    del tmp_path
    applied: list[AppConfig] = []

    async def apply(config: AppConfig) -> None:
        applied.append(config)

    service = ProviderService(
        AppConfig(),
        MemoryCredentialStore(),
        apply_config=apply,
        connected_aliases=lambda: (),
        environ={},
    )

    result = await service.save(provider_draft("offline"))

    assert result.config.primary_provider is None
    assert result.health.status is ProviderStatus.DISCONNECTED
    assert applied[-1].providers["offline"].model == "gpt-test"
    with pytest.raises(ProviderConfigurationError, match="尚未连接"):
        await service.set_default("offline")


@pytest.mark.asyncio
async def test_fallback_order_and_credential_deletion_use_public_service() -> None:
    store = MemoryCredentialStore()
    store.set("primary", "primary-secret")
    store.set("fallback", "fallback-secret")
    config = AppConfig(
        providers={
            "primary": ProviderConfig(
                protocol=ProviderProtocol.OPENAI_RESPONSES,
                model="primary-model",
                credential_id="primary",
            ),
            "fallback": ProviderConfig(
                protocol=ProviderProtocol.OPENAI_RESPONSES,
                model="fallback-model",
                credential_id="fallback",
            ),
        },
        primary_provider="primary",
    )
    applied: list[AppConfig] = []

    async def apply(updated: AppConfig) -> None:
        applied.append(updated)

    service = ProviderService(
        config,
        store,
        apply_config=apply,
        connected_aliases=lambda: ("primary", "fallback"),
        environ={},
    )

    updated = await service.set_fallback_chain(("fallback",))
    health = service.delete_credential("fallback")

    assert updated.fallback_chain == ("fallback",)
    assert applied[-1].fallback_chain == ("fallback",)
    assert store.get("fallback") is None
    assert health.credential_source is None


@pytest.mark.asyncio
async def test_connected_provider_becomes_default_and_probe_uses_saved_secret() -> None:
    store = MemoryCredentialStore()
    connected: set[str] = set()
    probed: list[tuple[str, str]] = []

    async def apply(config: AppConfig) -> None:
        connected.update(config.providers)

    async def load_models(provider: ProviderConfig, api_key: str) -> tuple[str, ...]:
        probed.append((provider.model, api_key))
        return ("gpt-a", "gpt-b")

    service = ProviderService(
        AppConfig(),
        store,
        apply_config=apply,
        connected_aliases=lambda: tuple(connected),
        model_loader=load_models,
        environ={},
    )

    result = await service.save(provider_draft("ready", secret="secret"))
    probe = await service.probe(provider_draft("ready"))

    assert result.config.primary_provider == "ready"
    assert result.health.status is ProviderStatus.READY
    assert store.get("ready") == "secret"
    assert probe.model_ids == ("gpt-a", "gpt-b")
    assert probe.health.loaded_model_count == 2
    assert probed == [("gpt-test", "secret")]
    assert service.health("ready").loaded_model_count == 2


@pytest.mark.asyncio
async def test_probe_accepts_draft_before_model_is_selected() -> None:
    async def load_models(provider: ProviderConfig, api_key: str) -> tuple[str, ...]:
        assert provider.model == "pending"
        assert api_key == "secret"
        return ("gpt-a",)

    async def no_apply(_config: AppConfig) -> None:
        pytest.fail("probe must not apply configuration")

    service = ProviderService(
        AppConfig(),
        MemoryCredentialStore(),
        apply_config=no_apply,
        connected_aliases=lambda: (),
        model_loader=load_models,
        environ={},
    )

    draft = replace(provider_draft("new", secret="secret"), model="")
    assert (await service.probe(draft)).model_ids == ("gpt-a",)


@pytest.mark.asyncio
async def test_save_restores_credential_when_config_apply_fails() -> None:
    store = MemoryCredentialStore()
    store.set("ready", "old-secret")

    async def fail(_config: AppConfig) -> None:
        raise OSError("disk full")

    service = ProviderService(
        AppConfig(),
        store,
        apply_config=fail,
        connected_aliases=lambda: (),
        environ={},
    )

    with pytest.raises(OSError, match="disk full"):
        await service.save(provider_draft("ready", secret="new-secret"))

    assert store.get("ready") == "old-secret"
    assert service.config == AppConfig()


def test_health_does_not_read_credential_store_when_environment_is_ready() -> None:
    draft = provider_draft("ready")
    config = AppConfig(
        providers={
            "ready": ProviderConfig(
                protocol=draft.protocol,
                model=draft.model,
                api_key_env=draft.api_key_env,
                credential_id=draft.credential_id,
            )
        },
        primary_provider="ready",
    )

    async def no_apply(_config: AppConfig) -> None:
        pytest.fail("health must not apply configuration")

    service = ProviderService(
        config,
        FailingCredentialStore(),
        apply_config=no_apply,
        connected_aliases=lambda: ("ready",),
        environ={"MISSING_TEST_KEY": "environment-secret"},
    )

    health = service.health("ready")
    assert health.status is ProviderStatus.READY
    assert health.credential_source == "MISSING_TEST_KEY"
