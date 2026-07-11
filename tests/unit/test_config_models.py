import pytest
from pydantic import ValidationError

from windcode.config import AppConfig, ProviderConfig, ProviderProtocol


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
