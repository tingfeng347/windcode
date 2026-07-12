from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

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
    provider_id: str | None = Field(default=None, min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    api_key_env: str | None = Field(default=None, min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    credential_id: str | None = Field(
        default=None, min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )
    base_url: str | None = None

    @model_validator(mode="after")
    def validate_credentials(self) -> Self:
        if self.api_key_env is None and self.credential_id is None:
            raise ValueError("provider requires api_key_env or credential_id")
        return self


class BudgetConfig(StrictModel):
    max_model_steps: int = Field(default=40, ge=1)
    max_tool_calls: int = Field(default=100, ge=1)
    max_runtime_seconds: float = Field(default=1800.0, gt=0)
    shell_timeout_seconds: float = Field(default=120.0, gt=0)


HARD_MAX_SUBAGENT_TASKS = 16
HARD_MAX_CONCURRENT_SUBAGENTS = 8


class DelegationMode(StrEnum):
    EXPLICIT = "explicit"
    PROACTIVE = "proactive"


class SubagentConfig(StrictModel):
    mode: DelegationMode = DelegationMode.EXPLICIT
    max_tasks: int = Field(default=8, ge=1, le=HARD_MAX_SUBAGENT_TASKS)
    max_concurrent: int = Field(default=4, ge=1, le=HARD_MAX_CONCURRENT_SUBAGENTS)
    max_model_steps: int = Field(default=20, ge=1)
    max_tool_calls: int = Field(default=50, ge=1)
    max_runtime_seconds: float = Field(default=900.0, gt=0)
    max_total_model_steps: int = Field(default=80, ge=1)
    max_total_tool_calls: int = Field(default=200, ge=1)

    @model_validator(mode="after")
    def validate_aggregate_limits(self) -> Self:
        if self.max_concurrent > self.max_tasks:
            raise ValueError("max_concurrent cannot exceed max_tasks")
        if self.max_total_model_steps < self.max_model_steps:
            raise ValueError("max_total_model_steps cannot be below max_model_steps")
        if self.max_total_tool_calls < self.max_tool_calls:
            raise ValueError("max_total_tool_calls cannot be below max_tool_calls")
        return self


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


class EnvironmentReference(StrictModel):
    env: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class CredentialReference(StrictModel):
    credential: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


SecretReference = Annotated[
    EnvironmentReference | CredentialReference, Field(union_mode="left_to_right")
]


class McpStdioConfig(StrictModel):
    transport: Literal["stdio"] = "stdio"
    command: str = Field(min_length=1)
    args: tuple[str, ...] = ()
    cwd: str | None = Field(default=None, min_length=1)
    env: dict[str, SecretReference] = Field(default_factory=dict[str, SecretReference])
    required: bool = False


class McpHttpConfig(StrictModel):
    transport: Literal["streamable_http"]
    url: str = Field(min_length=1, pattern=r"^https?://")
    headers: dict[str, SecretReference] = Field(default_factory=dict[str, SecretReference])
    required: bool = False


McpServerConfig = Annotated[McpStdioConfig | McpHttpConfig, Field(union_mode="left_to_right")]


class ExtensionConfig(StrictModel):
    enabled: bool = False
    direct_tool_limit: int = Field(default=24, ge=0, le=256)
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    call_timeout_seconds: float = Field(default=60.0, gt=0, le=3600)
    hook_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    max_metadata_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    max_content_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)
    max_scan_depth: int = Field(default=8, ge=1, le=32)
    max_scan_entries: int = Field(default=10_000, ge=1, le=100_000)
    skill_roots: tuple[str, ...] = ()
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict[str, McpServerConfig])
    project_mcp_servers: frozenset[str] = Field(default_factory=frozenset, exclude=True)


class AppConfig(StrictModel):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict[str, ProviderConfig])
    primary_provider: str | None = None
    fallback_chain: tuple[str, ...] = ()
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    permission: PermissionConfig = Field(default_factory=PermissionConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    trace: TraceConfig = Field(default_factory=TraceConfig)
    subagents: SubagentConfig = Field(default_factory=SubagentConfig)
    extensions: ExtensionConfig = Field(default_factory=ExtensionConfig)

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
