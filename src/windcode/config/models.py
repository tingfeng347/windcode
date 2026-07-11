from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderProtocol(StrEnum):
    ANTHROPIC_MESSAGES = "anthropic_messages"
    OPENAI_RESPONSES = "openai_responses"
    OPENAI_COMPATIBLE = "openai_compatible"


class ProviderConfig(StrictModel):
    protocol: ProviderProtocol
    model: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    base_url: str | None = None


class BudgetConfig(StrictModel):
    max_model_steps: int = Field(default=40, ge=1)
    max_tool_calls: int = Field(default=100, ge=1)
    max_runtime_seconds: float = Field(default=1800.0, gt=0)
    shell_timeout_seconds: float = Field(default=120.0, gt=0)


class PermissionMode(StrEnum):
    PLAN = "plan"
    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    FULL_ACCESS = "full_access"


class PermissionConfig(StrictModel):
    mode: PermissionMode = PermissionMode.DEFAULT


class SandboxConfig(StrictModel):
    enabled: bool = True
    network_enabled: bool = False


class ContextConfig(StrictModel):
    window_tokens: int = Field(default=128_000, ge=1_024)
    compaction_threshold: float = Field(default=0.8, gt=0.0, lt=1.0)
    preserve_recent_turns: int = Field(default=8, ge=1)
    max_tool_result_chars: int = Field(default=20_000, ge=1_000)


class TraceConfig(StrictModel):
    enabled: bool = True
    include_tool_arguments: bool = False


class AppConfig(StrictModel):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict[str, ProviderConfig])
    primary_provider: str | None = None
    fallback_chain: tuple[str, ...] = ()
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    permission: PermissionConfig = Field(default_factory=PermissionConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    trace: TraceConfig = Field(default_factory=TraceConfig)

    @model_validator(mode="after")
    def validate_provider_chain(self) -> Self:
        if self.primary_provider is None:
            if self.fallback_chain:
                raise ValueError("fallback_chain requires primary_provider")
            return self

        chain = (self.primary_provider, *self.fallback_chain)
        missing = [name for name in chain if name not in self.providers]
        if missing:
            raise ValueError(f"provider chain references unknown providers: {', '.join(missing)}")
        if len(set(chain)) != len(chain):
            raise ValueError("provider chain contains a duplicate or cycle")
        return self
