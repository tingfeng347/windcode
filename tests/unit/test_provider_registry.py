from collections.abc import AsyncIterator
from pathlib import Path

import pytest

import windcode.providers.registry as registry_module
from windcode.application import ConfigurationApplication, ProviderApplication
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


class CountingTransport(FakeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.close_count = 0

    async def aclose(self) -> None:
        self.close_count += 1
        await super().aclose()


class FailingCloseTransport(FakeTransport):
    async def aclose(self) -> None:
        raise RuntimeError("close failed")


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


def provider_application(config: AppConfig | None = None) -> ProviderApplication:
    return ProviderApplication(
        ConfigurationApplication(config or AppConfig()), FakeCredentialStore(None)
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


def test_empty_model_chain_uses_provider_configuration_error() -> None:
    application = provider_application()

    with pytest.raises(ProviderConfigurationError, match="no runnable model provider"):
        application.resolve(None)


def test_provider_application_registers_and_resolves_without_opening() -> None:
    application = provider_application()
    transport = FakeTransport()

    application.register("custom", "model", transport, primary=True)

    assert application.can_resolve()
    assert application.resolve(None)[0].transport is transport
    assert application.startup_error is None


@pytest.mark.asyncio
async def test_sdk_transport_registered_before_enter_remains_available(tmp_path: Path) -> None:
    from windcode.sdk import Windcode

    transport = FakeTransport()
    client = Windcode.open(state_root=tmp_path / "state")
    client.register_transport("custom", "model", transport, primary=True)

    async with client:
        assert client.can_resolve_model()
        assert client.transport_registry.get("custom").transport is transport

    assert transport.closed


@pytest.mark.asyncio
async def test_closes_registered_transports_once() -> None:
    registry = TransportRegistry()
    shared = FakeTransport()
    registry.register("one", "model", shared)
    registry.register("two", "model", shared)

    await registry.aclose()

    assert shared.closed


@pytest.mark.asyncio
async def test_reconfigure_rejects_unrunnable_primary_before_persisting(tmp_path: Path) -> None:
    from windcode.sdk import Windcode

    client = Windcode.open(state_root=tmp_path / "state")
    config_file = tmp_path / "config.toml"
    candidate = AppConfig(
        providers={
            "offline": ProviderConfig(
                protocol=ProviderProtocol.OPENAI_RESPONSES,
                model="model",
                api_key_env="MISSING_TEST_KEY",
            )
        },
        primary_provider="offline",
    )

    with pytest.raises(ProviderConfigurationError, match="not runnable"):
        await client.reconfigure_models(candidate, config_file=config_file)

    assert not config_file.exists()
    assert client.config == AppConfig()
    assert client.transport_registry.aliases == ()


@pytest.mark.asyncio
async def test_provider_application_reconfigure_swaps_then_closes_previous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    old_transport = CountingTransport()
    new_transport = CountingTransport()

    def create_new_transport(provider: ProviderConfig, api_key: str) -> CountingTransport:
        del provider, api_key
        return new_transport

    application = provider_application()
    application.register("old", "old-model", old_transport, primary=True)
    candidate = AppConfig(
        providers={
            "new": ProviderConfig(
                protocol=ProviderProtocol.OPENAI_RESPONSES,
                model="new-model",
                api_key_env="NEW_KEY",
            )
        },
        primary_provider="new",
    )
    monkeypatch.setattr(registry_module, "create_transport", create_new_transport)
    monkeypatch.setenv("NEW_KEY", "secret")

    await application.reconfigure(candidate, config_file=tmp_path / "config.toml")

    assert application.configuration.current == candidate
    assert application.resolve(None)[0].transport is new_transport
    assert old_transport.close_count == 1
    assert new_transport.close_count == 0


@pytest.mark.asyncio
async def test_provider_application_closes_candidate_when_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current_transport = CountingTransport()
    candidate_transport = CountingTransport()

    def create_candidate_transport(provider: ProviderConfig, api_key: str) -> CountingTransport:
        del provider, api_key
        return candidate_transport

    def fail_save(path: Path, previous: AppConfig, updated: AppConfig) -> None:
        del path, previous, updated
        raise OSError("write failed")

    application = provider_application()
    application.register("current", "model", current_transport, primary=True)
    candidate = AppConfig(
        providers={
            "candidate": ProviderConfig(
                protocol=ProviderProtocol.OPENAI_RESPONSES,
                model="candidate-model",
                api_key_env="CANDIDATE_KEY",
            )
        },
        primary_provider="candidate",
    )
    monkeypatch.setattr(registry_module, "create_transport", create_candidate_transport)
    monkeypatch.setattr(
        "windcode.application.configuration.save_model_config",
        fail_save,
    )
    monkeypatch.setenv("CANDIDATE_KEY", "secret")

    with pytest.raises(OSError, match="write failed"):
        await application.reconfigure(candidate, config_file=tmp_path / "config.toml")

    assert application.configuration.current == AppConfig()
    assert application.resolve(None)[0].transport is current_transport
    assert current_transport.close_count == 0
    assert candidate_transport.close_count == 1


@pytest.mark.asyncio
async def test_provider_application_publishes_candidate_before_previous_close_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate_transport = CountingTransport()

    def create_candidate_transport(provider: ProviderConfig, api_key: str) -> CountingTransport:
        del provider, api_key
        return candidate_transport

    application = provider_application()
    application.register("old", "model", FailingCloseTransport(), primary=True)
    candidate = AppConfig(
        providers={
            "candidate": ProviderConfig(
                protocol=ProviderProtocol.OPENAI_RESPONSES,
                model="candidate-model",
                api_key_env="CANDIDATE_KEY",
            )
        },
        primary_provider="candidate",
    )
    monkeypatch.setattr(registry_module, "create_transport", create_candidate_transport)
    monkeypatch.setenv("CANDIDATE_KEY", "secret")

    with pytest.raises(RuntimeError, match="close failed"):
        await application.reconfigure(candidate, config_file=tmp_path / "config.toml")

    assert application.configuration.current == candidate
    assert application.resolve(None)[0].transport is candidate_transport
