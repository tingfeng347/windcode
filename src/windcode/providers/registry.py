from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from windcode.config.models import AppConfig, ProviderConfig, ProviderProtocol
from windcode.providers.anthropic import AnthropicTransport
from windcode.providers.base import ModelTransport
from windcode.providers.openai_compat import OpenAICompatibleTransport
from windcode.providers.openai_responses import OpenAIResponsesTransport


class ProviderConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ModelTarget:
    provider: str
    model: str
    transport: ModelTransport


def create_transport(config: ProviderConfig, api_key: str) -> ModelTransport:
    if config.protocol is ProviderProtocol.ANTHROPIC_MESSAGES:
        return AnthropicTransport(api_key=api_key, base_url=config.base_url)
    if config.protocol is ProviderProtocol.OPENAI_RESPONSES:
        return OpenAIResponsesTransport(api_key=api_key, base_url=config.base_url)
    if config.base_url is None:
        raise ProviderConfigurationError("openai_compatible provider requires base_url")
    return OpenAICompatibleTransport(api_key=api_key, base_url=config.base_url)


class TransportRegistry:
    def __init__(self) -> None:
        self._targets: dict[str, ModelTarget] = {}

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> TransportRegistry:
        registry = cls()
        environment = os.environ if environ is None else environ
        for alias, provider in config.providers.items():
            api_key = environment.get(provider.api_key_env)
            if not api_key:
                raise ProviderConfigurationError(
                    f"provider {alias!r} requires environment variable {provider.api_key_env}"
                )
            registry.register(alias, provider.model, create_transport(provider, api_key))
        return registry

    def register(
        self,
        alias: str,
        model: str,
        transport: ModelTransport,
        *,
        replace: bool = False,
    ) -> None:
        if alias in self._targets and not replace:
            raise ValueError(f"transport alias already registered: {alias}")
        self._targets[alias] = ModelTarget(alias, model, transport)

    def get(self, alias: str) -> ModelTarget:
        try:
            return self._targets[alias]
        except KeyError as exc:
            raise KeyError(f"unknown transport alias: {alias}") from exc

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(self._targets)

    def resolve_chain(self, config: AppConfig) -> tuple[ModelTarget, ...]:
        if config.primary_provider is None:
            raise ProviderConfigurationError("no primary_provider is configured")
        aliases = (config.primary_provider, *config.fallback_chain)
        if len(set(aliases)) != len(aliases):
            raise ProviderConfigurationError("provider fallback chain contains a cycle")
        return tuple(self.get(alias) for alias in aliases)

    async def aclose(self) -> None:
        closed: set[int] = set()
        for target in reversed(tuple(self._targets.values())):
            identity = id(target.transport)
            if identity not in closed:
                await target.transport.aclose()
                closed.add(identity)
