from collections.abc import AsyncIterator

import pytest

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


@pytest.mark.asyncio
async def test_closes_registered_transports_once() -> None:
    registry = TransportRegistry()
    shared = FakeTransport()
    registry.register("one", "model", shared)
    registry.register("two", "model", shared)

    await registry.aclose()

    assert shared.closed
