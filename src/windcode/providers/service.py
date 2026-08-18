from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from windcode.auth import CredentialStore, CredentialStoreError
from windcode.config import AppConfig, ProviderConfig, ProviderProtocol
from windcode.providers.catalog import PRESETS_BY_ID
from windcode.providers.models import fetch_model_ids
from windcode.providers.registry import ProviderConfigurationError

_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

ApplyProviderConfig = Callable[[AppConfig], Awaitable[None]]
ConnectedAliases = Callable[[], tuple[str, ...]]
ModelLoader = Callable[[ProviderConfig, str], Awaitable[tuple[str, ...]]]


class ProviderStatus(StrEnum):
    READY = "ready"
    DISCONNECTED = "disconnected"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ProviderDraft:
    alias: str
    protocol: ProviderProtocol
    model: str
    provider_id: str | None
    api_key_env: str | None
    credential_id: str | None
    base_url: str | None
    secret: str | None = None
    editing_alias: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    alias: str
    provider: ProviderConfig
    status: ProviderStatus
    is_default: bool
    credential_source: str | None
    environment_set: bool
    loaded_model_count: int = 0
    diagnostic: str | None = None

    @property
    def connected(self) -> bool:
        return self.status is ProviderStatus.READY


@dataclass(frozen=True, slots=True)
class ProviderApplyResult:
    config: AppConfig
    health: ProviderHealth


@dataclass(frozen=True, slots=True)
class ProviderProbeResult:
    model_ids: tuple[str, ...]
    health: ProviderHealth


class ProviderService:
    """Own the Provider configuration lifecycle behind one application seam."""

    def __init__(
        self,
        config: AppConfig,
        credential_store: CredentialStore,
        *,
        apply_config: ApplyProviderConfig,
        connected_aliases: ConnectedAliases,
        model_loader: ModelLoader = fetch_model_ids,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.credential_store = credential_store
        self._apply_config = apply_config
        self._connected_aliases = connected_aliases
        self._model_loader = model_loader
        self._environ = os.environ if environ is None else environ
        self._loaded_model_counts: dict[str, int] = {}

    def snapshot(self) -> tuple[ProviderHealth, ...]:
        return tuple(self.health(alias) for alias in self.config.providers)

    def update_config(self, config: AppConfig) -> None:
        """Rebase non-Provider settings changed by another application service."""
        self.config = config

    def health(self, alias: str) -> ProviderHealth:
        try:
            provider = self.config.providers[alias]
        except KeyError as exc:
            raise KeyError(f"unknown provider: {alias}") from exc
        environment_set = bool(provider.api_key_env and self._environ.get(provider.api_key_env))
        credential_set = False
        diagnostic: str | None = None
        if not environment_set and provider.credential_id is not None:
            try:
                credential_set = bool(self.credential_store.get(provider.credential_id))
            except CredentialStoreError as exc:
                diagnostic = str(exc)
        connected = alias in self._connected_aliases()
        if diagnostic is not None:
            status = ProviderStatus.ERROR
        elif connected:
            status = ProviderStatus.READY
        else:
            status = ProviderStatus.DISCONNECTED
        source = (
            provider.api_key_env
            if environment_set
            else provider.credential_id
            if credential_set
            else None
        )
        return ProviderHealth(
            alias,
            provider,
            status,
            alias == self.config.primary_provider,
            source,
            environment_set,
            self._loaded_model_counts.get(alias, 0),
            diagnostic,
        )

    def draft(self, alias: str | None = None, *, preset_id: str | None = None) -> ProviderDraft:
        if alias is not None:
            provider = self.config.providers[alias]
            return ProviderDraft(
                alias,
                provider.protocol,
                provider.model,
                provider.provider_id,
                provider.api_key_env,
                provider.credential_id,
                provider.base_url,
                editing_alias=alias,
            )
        preset = PRESETS_BY_ID.get(preset_id or "openai") or PRESETS_BY_ID["openai"]
        return ProviderDraft(
            preset.id,
            preset.protocol,
            "",
            preset.id,
            preset.api_key_env,
            preset.id,
            preset.base_url,
        )

    def _provider_from_draft(self, draft: ProviderDraft, *, require_model: bool) -> ProviderConfig:
        alias = draft.alias.strip()
        if not _ALIAS_PATTERN.fullmatch(alias):
            raise ProviderConfigurationError("别名只能包含字母、数字、点、下划线和连字符")
        if draft.editing_alias is None and alias in self.config.providers:
            raise ProviderConfigurationError(f"Provider 已存在: {alias}")
        model = draft.model.strip()
        if require_model and not model:
            raise ProviderConfigurationError("请填写模型 ID, 或先加载并选择可用模型")
        try:
            provider = ProviderConfig(
                protocol=draft.protocol,
                model=model or "pending",
                provider_id=draft.provider_id,
                api_key_env=draft.api_key_env.strip() if draft.api_key_env else None,
                credential_id=draft.credential_id or alias,
                base_url=draft.base_url.strip() if draft.base_url else None,
            )
        except (TypeError, ValueError) as exc:
            raise ProviderConfigurationError(str(exc)) from exc
        if provider.protocol is ProviderProtocol.OPENAI_COMPATIBLE and not provider.base_url:
            raise ProviderConfigurationError("OpenAI Compatible 协议必须填写 Base URL")
        return provider

    def validate(self, draft: ProviderDraft) -> ProviderConfig:
        return self._provider_from_draft(draft, require_model=True)

    def _config_with(
        self,
        providers: dict[str, ProviderConfig],
        *,
        primary: str | None,
        fallback: tuple[str, ...],
    ) -> AppConfig:
        data = self.config.model_dump(mode="python")
        data.update(
            providers=providers,
            primary_provider=primary,
            fallback_chain=fallback,
        )
        return AppConfig.model_validate(data)

    def _resolve_secret(self, provider: ProviderConfig, supplied: str | None) -> str | None:
        if supplied:
            return supplied
        if provider.api_key_env:
            value = self._environ.get(provider.api_key_env)
            if value:
                return value
        if provider.credential_id:
            return self.credential_store.get(provider.credential_id)
        return None

    async def save(self, draft: ProviderDraft) -> ProviderApplyResult:
        provider = self.validate(draft)
        alias = draft.alias.strip()
        previous_secret: str | None = None
        secret_changed = draft.secret is not None and provider.credential_id is not None
        if secret_changed and provider.credential_id is not None:
            previous_secret = self.credential_store.get(provider.credential_id)
            self.credential_store.set(provider.credential_id, draft.secret or "")
        providers = dict(self.config.providers)
        providers[alias] = provider
        has_secret = bool(self._resolve_secret(provider, draft.secret))
        primary = self.config.primary_provider or (alias if has_secret else None)
        candidate = self._config_with(
            providers,
            primary=primary,
            fallback=self.config.fallback_chain,
        )
        try:
            await self._apply_config(candidate)
        except Exception:
            if secret_changed and provider.credential_id is not None:
                if previous_secret is None:
                    self.credential_store.delete(provider.credential_id)
                else:
                    self.credential_store.set(provider.credential_id, previous_secret)
            raise
        self.config = candidate
        return ProviderApplyResult(candidate, self.health(alias))

    async def probe(self, draft: ProviderDraft) -> ProviderProbeResult:
        alias = draft.alias.strip()
        if draft.editing_alias is None and alias in self.config.providers:
            draft = replace(draft, editing_alias=alias)
        provider = self._provider_from_draft(draft, require_model=False)
        secret = self._resolve_secret(provider, draft.secret)
        if not secret:
            raise ProviderConfigurationError("填写 API Key 或配置对应环境变量后即可加载模型")
        model_ids = await self._model_loader(provider, secret)
        if not model_ids:
            raise ProviderConfigurationError("Provider 未返回可用模型 ID, 请手动填写")
        alias = draft.alias.strip()
        self._loaded_model_counts[alias] = len(model_ids)
        environment_set = bool(provider.api_key_env and self._environ.get(provider.api_key_env))
        credential_source = (
            provider.api_key_env
            if environment_set
            else provider.credential_id
            if provider.credential_id
            else "draft"
        )
        return ProviderProbeResult(
            model_ids,
            ProviderHealth(
                alias,
                provider,
                ProviderStatus.READY,
                alias == self.config.primary_provider,
                credential_source,
                environment_set,
                len(model_ids),
                "连接测试成功",
            ),
        )

    async def set_default(self, alias: str) -> ProviderApplyResult:
        health = self.health(alias)
        if not health.connected:
            raise ProviderConfigurationError(f"Provider {alias} 尚未连接, 不能设为默认")
        ordered = (self.config.primary_provider, *self.config.fallback_chain)
        fallback = tuple(
            item
            for item in ordered
            if item is not None and item != alias and item in self.config.providers
        )
        candidate = self._config_with(dict(self.config.providers), primary=alias, fallback=fallback)
        await self._apply_config(candidate)
        self.config = candidate
        return ProviderApplyResult(candidate, self.health(alias))

    async def set_fallback_chain(self, aliases: tuple[str, ...]) -> AppConfig:
        primary = self.config.primary_provider
        if primary is None and aliases:
            raise ProviderConfigurationError("请先设置默认 Provider")
        if len(set(aliases)) != len(aliases):
            raise ProviderConfigurationError("fallback chain 不能包含重复 Provider")
        invalid = [
            alias for alias in aliases if alias == primary or alias not in self.config.providers
        ]
        if invalid:
            raise ProviderConfigurationError(
                f"fallback chain 包含无效 Provider: {', '.join(invalid)}"
            )
        candidate = self._config_with(
            dict(self.config.providers), primary=primary, fallback=aliases
        )
        await self._apply_config(candidate)
        self.config = candidate
        return candidate

    def delete_credential(self, alias: str) -> ProviderHealth:
        provider = self.config.providers.get(alias)
        if provider is None:
            raise KeyError(f"unknown provider: {alias}")
        if provider.credential_id is not None:
            self.credential_store.delete(provider.credential_id)
        return self.health(alias)

    async def delete(self, alias: str) -> AppConfig:
        provider = self.config.providers.get(alias)
        if provider is None:
            return self.config
        providers = dict(self.config.providers)
        providers.pop(alias)
        primary = self.config.primary_provider
        if primary == alias:
            primary = next(
                (item for item in providers if item in self._connected_aliases()),
                None,
            )
        fallback = tuple(
            item
            for item in self.config.fallback_chain
            if item != alias and item != primary and item in providers
        )
        previous_secret = (
            self.credential_store.get(provider.credential_id)
            if provider.credential_id is not None
            else None
        )
        if provider.credential_id is not None:
            self.credential_store.delete(provider.credential_id)
        candidate = self._config_with(providers, primary=primary, fallback=fallback)
        try:
            await self._apply_config(candidate)
        except Exception:
            if provider.credential_id is not None and previous_secret is not None:
                self.credential_store.set(provider.credential_id, previous_secret)
            raise
        self.config = candidate
        self._loaded_model_counts.pop(alias, None)
        return candidate
