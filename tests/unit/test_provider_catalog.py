from windcode.config import ProviderConfig, ProviderProtocol
from windcode.providers.catalog import PRESETS_BY_ID, PROVIDER_PRESETS, provider_preset


def test_builtin_provider_ids_and_endpoints_are_unique() -> None:
    assert len(PROVIDER_PRESETS) >= 10
    assert len({preset.id for preset in PROVIDER_PRESETS}) == len(PROVIDER_PRESETS)
    assert all(preset.base_url.startswith("https://") for preset in PROVIDER_PRESETS)


def test_catalog_contains_opencode_compatible_provider_metadata() -> None:
    assert PRESETS_BY_ID["deepseek"].base_url == "https://api.deepseek.com/v1"
    assert PRESETS_BY_ID["openrouter"].base_url == "https://openrouter.ai/api/v1"
    assert PRESETS_BY_ID["moonshotai"].api_key_env == "MOONSHOT_API_KEY"
    assert PRESETS_BY_ID["siliconflow"].api_key_env == "SILICONFLOW_API_KEY"


def test_provider_preset_uses_explicit_id_then_recognizes_existing_config() -> None:
    explicit = ProviderConfig(
        protocol=ProviderProtocol.OPENAI_COMPATIBLE,
        model="model",
        provider_id="deepseek",
        credential_id="deepseek",
        base_url="https://custom.example/v1",
    )
    legacy = ProviderConfig(
        protocol=ProviderProtocol.OPENAI_COMPATIBLE,
        model="model",
        credential_id="openrouter",
        base_url="https://openrouter.ai/api/v1/",
    )

    assert provider_preset(explicit) is PRESETS_BY_ID["deepseek"]
    assert provider_preset(legacy) is PRESETS_BY_ID["openrouter"]
