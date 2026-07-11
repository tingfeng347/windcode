import pytest
from pydantic import ValidationError

from windcode.config import (
    HARD_MAX_CONCURRENT_SUBAGENTS,
    HARD_MAX_SUBAGENT_TASKS,
    AppConfig,
    DelegationMode,
    ProviderConfig,
    ProviderProtocol,
    SubagentConfig,
)


def provider(model: str = "test-model") -> ProviderConfig:
    return ProviderConfig(
        protocol=ProviderProtocol.OPENAI_RESPONSES,
        model=model,
        api_key_env="OPENAI_API_KEY",
    )


def test_valid_provider_chain() -> None:
    config = AppConfig(
        providers={"primary": provider(), "backup": provider("backup-model")},
        primary_provider="primary",
        fallback_chain=("backup",),
    )

    assert config.fallback_chain == ("backup",)


@pytest.mark.parametrize(
    "data",
    [
        {"providers": {"main": provider()}, "primary_provider": "missing"},
        {
            "providers": {"main": provider()},
            "primary_provider": "main",
            "fallback_chain": ["main"],
        },
        {"fallback_chain": ["missing"]},
        {"api_key": "secret"},
    ],
)
def test_invalid_configuration_is_rejected(data: object) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_plaintext_api_key_is_not_a_provider_field() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig.model_validate(
            {
                "protocol": "openai_responses",
                "model": "model",
                "api_key_env": "OPENAI_API_KEY",
                "api_key": "secret",
            }
        )


def test_provider_accepts_persisted_credential_without_environment_variable() -> None:
    configured = ProviderConfig(
        protocol=ProviderProtocol.OPENAI_RESPONSES,
        model="model",
        credential_id="openai",
    )

    assert configured.api_key_env is None


def test_provider_requires_a_credential_source() -> None:
    with pytest.raises(ValidationError, match="api_key_env or credential_id"):
        ProviderConfig(protocol=ProviderProtocol.OPENAI_RESPONSES, model="model")


def test_subagent_defaults_and_valid_override() -> None:
    defaults = AppConfig().subagents
    assert defaults.mode is DelegationMode.EXPLICIT
    assert (defaults.max_tasks, defaults.max_concurrent) == (8, 4)

    configured = SubagentConfig(
        mode=DelegationMode.PROACTIVE,
        max_tasks=6,
        max_concurrent=3,
        max_model_steps=10,
        max_tool_calls=15,
        max_total_model_steps=30,
        max_total_tool_calls=45,
    )
    assert configured.mode is DelegationMode.PROACTIVE


@pytest.mark.parametrize(
    "data",
    [
        {"max_tasks": HARD_MAX_SUBAGENT_TASKS + 1},
        {"max_concurrent": HARD_MAX_CONCURRENT_SUBAGENTS + 1},
        {"max_tasks": 2, "max_concurrent": 3},
        {"max_model_steps": 20, "max_total_model_steps": 19},
        {"max_tool_calls": 50, "max_total_tool_calls": 49},
    ],
)
def test_invalid_subagent_limits_are_rejected(data: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        SubagentConfig.model_validate(data)
