from collections.abc import AsyncIterator

import pytest

import windcode.providers.registry as registry_module
from windcode.config import AppConfig, ProviderConfig, ProviderProtocol
from windcode.domain.models import ModelEvent, ModelRequest
from windcode.providers import ProviderConfigurationError, TransportRegistry


class FakeTransport:
    name = "fake"

    def __init__(self) -> None:
        self.closed = False

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        if False:
            yield

    async def aclose(self) -> None:
        self.closed = True


class FakeCredentialStore:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get(self, credential_id: str) -> str | None:
        del credential_id
        return self.value

    def set(self, credential_id: str, secret: str) -> None:
        del credential_id, secret

    def delete(self, credential_id: str) -> None:
        del credential_id


def config() -> AppConfig:
    return AppConfig(
        providers={
            "primary": ProviderConfig(
                protocol=ProviderProtocol.OPENAI_RESPONSES,
                model="primary-model",
                api_key_env="PRIMARY_KEY",
            ),
            "backup": ProviderConfig(
                protocol=ProviderProtocol.ANTHROPIC_MESSAGES,
                model="backup-model",
                api_key_env="BACKUP_KEY",
            ),
        },
        primary_provider="primary",
        fallback_chain=("backup",),
    )


def test_resolves_custom_transports_in_configured_order() -> None:
    registry = TransportRegistry()
    primary = FakeTransport()
    backup = FakeTransport()
    registry.register("primary", "primary-model", primary)
    registry.register("backup", "backup-model", backup)

    chain = registry.resolve_chain(config())

    assert [(target.provider, target.model) for target in chain] == [
        ("primary", "primary-model"),
        ("backup", "backup-model"),
    ]


def test_rejects_duplicate_registration() -> None:
    registry = TransportRegistry()
    registry.register("provider", "model", FakeTransport())

    with pytest.raises(ValueError, match="already registered"):
        registry.register("provider", "model", FakeTransport())


def test_missing_api_key_has_actionable_diagnostic() -> None:
    with pytest.raises(ProviderConfigurationError, match="PRIMARY_KEY"):
        TransportRegistry.from_config(config(), environ={})


def test_loads_persisted_credential_when_environment_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def capture_transport(provider: ProviderConfig, api_key: str) -> FakeTransport:
        del provider
        captured.append(api_key)
        return FakeTransport()

    monkeypatch.setattr(
        registry_module,
        "create_transport",
        capture_transport,
    )
    persisted = AppConfig(
        providers={
            "main": ProviderConfig(
                protocol=ProviderProtocol.OPENAI_RESPONSES,
                model="model",
                credential_id="main",
            )
        },
        primary_provider="main",
    )

    TransportRegistry.from_config(
        persisted, environ={}, credential_store=FakeCredentialStore("saved-secret")
    )

    assert captured == ["saved-secret"]


def test_environment_variable_overrides_persisted_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def capture_transport(provider: ProviderConfig, api_key: str) -> FakeTransport:
        del provider
        captured.append(api_key)
        return FakeTransport()

    monkeypatch.setattr(
        registry_module,
        "create_transport",
        capture_transport,
    )
    persisted = AppConfig(
        providers={
            "main": ProviderConfig(
                protocol=ProviderProtocol.OPENAI_RESPONSES,
                model="model",
                api_key_env="MODEL_API_KEY",
                credential_id="main",
            )
        },
        primary_provider="main",
    )

    TransportRegistry.from_config(
        persisted,
        environ={"MODEL_API_KEY": "environment-secret"},
        credential_store=FakeCredentialStore("saved-secret"),
    )

    assert captured == ["environment-secret"]


def test_can_keep_disconnected_provider_metadata() -> None:
    registry = TransportRegistry.from_config(config(), environ={}, allow_missing=True)

    assert registry.aliases == ()


@pytest.mark.asyncio
async def test_closes_registered_transports_once() -> None:
    registry = TransportRegistry()
    shared = FakeTransport()
    registry.register("one", "model", shared)
    registry.register("two", "model", shared)

    await registry.aclose()

    assert shared.closed
