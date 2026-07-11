from __future__ import annotations

from dataclasses import dataclass

from windcode.config.models import ProviderConfig, ProviderProtocol


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    id: str
    name: str
    protocol: ProviderProtocol
    api_key_env: str
    base_url: str


PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        "openai",
        "OpenAI",
        ProviderProtocol.OPENAI_RESPONSES,
        "OPENAI_API_KEY",
        "https://api.openai.com/v1",
    ),
    ProviderPreset(
        "anthropic",
        "Anthropic",
        ProviderProtocol.ANTHROPIC_MESSAGES,
        "ANTHROPIC_API_KEY",
        "https://api.anthropic.com",
    ),
    ProviderPreset(
        "deepseek",
        "DeepSeek",
        ProviderProtocol.OPENAI_COMPATIBLE,
        "DEEPSEEK_API_KEY",
        "https://api.deepseek.com/v1",
    ),
    ProviderPreset(
        "moonshotai",
        "Moonshot AI",
        ProviderProtocol.OPENAI_COMPATIBLE,
        "MOONSHOT_API_KEY",
        "https://api.moonshot.ai/v1",
    ),
    ProviderPreset(
        "siliconflow",
        "SiliconFlow",
        ProviderProtocol.OPENAI_COMPATIBLE,
        "SILICONFLOW_API_KEY",
        "https://api.siliconflow.com/v1",
    ),
    ProviderPreset(
        "openrouter",
        "OpenRouter",
        ProviderProtocol.OPENAI_COMPATIBLE,
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/api/v1",
    ),
    ProviderPreset(
        "zhipuai",
        "Zhipu AI",
        ProviderProtocol.OPENAI_COMPATIBLE,
        "ZHIPU_API_KEY",
        "https://open.bigmodel.cn/api/paas/v4",
    ),
    ProviderPreset(
        "alibaba",
        "Alibaba Cloud",
        ProviderProtocol.OPENAI_COMPATIBLE,
        "DASHSCOPE_API_KEY",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    ProviderPreset(
        "groq",
        "Groq",
        ProviderProtocol.OPENAI_COMPATIBLE,
        "GROQ_API_KEY",
        "https://api.groq.com/openai/v1",
    ),
    ProviderPreset(
        "mistral",
        "Mistral",
        ProviderProtocol.OPENAI_COMPATIBLE,
        "MISTRAL_API_KEY",
        "https://api.mistral.ai/v1",
    ),
    ProviderPreset(
        "xai", "xAI", ProviderProtocol.OPENAI_COMPATIBLE, "XAI_API_KEY", "https://api.x.ai/v1"
    ),
    ProviderPreset(
        "google",
        "Google Gemini",
        ProviderProtocol.OPENAI_COMPATIBLE,
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/openai",
    ),
)

PRESETS_BY_ID = {preset.id: preset for preset in PROVIDER_PRESETS}


def provider_preset(config: ProviderConfig) -> ProviderPreset | None:
    if config.provider_id is not None:
        preset = PRESETS_BY_ID.get(config.provider_id)
        if preset is not None:
            return preset
    normalized_url = (config.base_url or "").rstrip("/").casefold()
    for preset in PROVIDER_PRESETS:
        if (
            config.protocol is preset.protocol
            and normalized_url == preset.base_url.rstrip("/").casefold()
        ):
            return preset
    if config.protocol is ProviderProtocol.OPENAI_RESPONSES:
        return PRESETS_BY_ID["openai"]
    if config.protocol is ProviderProtocol.ANTHROPIC_MESSAGES:
        return PRESETS_BY_ID["anthropic"]
    return None
