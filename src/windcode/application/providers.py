from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from windcode.auth import CredentialStore, CredentialStoreError
from windcode.config import AppConfig, save_model_config
from windcode.providers import (
    ModelTarget,
    ModelTransport,
    ProviderConfigurationError,
    TransportRegistry,
)


class ProviderApplication:
    """Own model transport state and its configuration transaction."""

    def __init__(self, config: AppConfig, credential_store: CredentialStore) -> None:
        self.config = config
        self.credential_store = credential_store
        self.registry = TransportRegistry()
        self.startup_error: str | None = None
        self._default_chain: list[str] = []

    async def open(self) -> None:
        if not self.config.providers:
            return
        try:
            registry = TransportRegistry.from_config(
                self.config,
                credential_store=self.credential_store,
                allow_missing=True,
            )
        except (CredentialStoreError, ProviderConfigurationError) as exc:
            self.registry = TransportRegistry()
            self.startup_error = str(exc)
            return
        self.registry = registry
        if self.config.primary_provider is not None:
            self._default_chain = [
                alias
                for alias in (self.config.primary_provider, *self.config.fallback_chain)
                if alias in registry.aliases
            ]

    def register(
        self,
        alias: str,
        model: str,
        transport: ModelTransport,
        *,
        replace_existing: bool = False,
        primary: bool = False,
    ) -> None:
        self.registry.register(alias, model, transport, replace=replace_existing)
        self.startup_error = None
        if primary or not self._default_chain:
            self._default_chain = [alias]

    async def reconfigure(self, config: AppConfig, *, config_file: Path) -> None:
        registry = (
            TransportRegistry.from_config(
                config,
                credential_store=self.credential_store,
                allow_missing=True,
            )
            if config.providers
            else TransportRegistry()
        )
        if config.primary_provider is not None and config.primary_provider not in registry.aliases:
            await registry.aclose()
            raise ProviderConfigurationError(
                f"primary provider {config.primary_provider!r} is not runnable; "
                "configure its credential or choose a connected provider"
            )
        try:
            save_model_config(config_file, self.config, config)
        except Exception:
            await registry.aclose()
            raise

        previous_registry = self.registry
        self.registry = registry
        self.startup_error = None
        self.config = config
        configured_chain = (
            (config.primary_provider, *config.fallback_chain)
            if config.primary_provider is not None
            else ()
        )
        self._default_chain = [alias for alias in configured_chain if alias in registry.aliases]
        await previous_registry.aclose()

    def resolve(self, requested: str | None) -> tuple[ModelTarget, ...]:
        if requested is not None and requested in self.registry.aliases:
            return (self.registry.get(requested),)
        if not self._default_chain:
            raise ProviderConfigurationError("no runnable model provider is configured")
        chain = tuple(self.registry.get(alias) for alias in self._default_chain)
        if requested is not None:
            chain = (replace(chain[0], model=requested), *chain[1:])
        return chain

    def can_resolve(self, requested: str | None = None) -> bool:
        return (requested is not None and requested in self.registry.aliases) or bool(
            self._default_chain
        )

    async def aclose(self) -> None:
        await self.registry.aclose()
