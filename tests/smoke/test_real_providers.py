import os
from collections.abc import AsyncIterator

import pytest

from windcode.domain.models import ModelRequest, TextDelta
from windcode.providers import (
    AnthropicTransport,
    ModelTransport,
    OpenAICompatibleTransport,
    OpenAIResponsesTransport,
)

pytestmark = pytest.mark.skipif(
    os.getenv("WINDCODE_REAL_SMOKE") != "1",
    reason="set WINDCODE_REAL_SMOKE=1 to run real-provider smoke tests",
)


async def text_events(transport: ModelTransport, model: str) -> AsyncIterator[str]:
    request = ModelRequest(
        model=model,
        messages=(),
        system_prompt="Reply with exactly: windcode-smoke",
        max_output_tokens=32,
    )
    async for event in transport.stream(request):
        if isinstance(event, TextDelta):
            yield event.text


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "openai", "compatible"])
async def test_real_provider_short_response(provider: str) -> None:
    if provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY")
        model = os.getenv("WINDCODE_ANTHROPIC_SMOKE_MODEL")
        if not key or not model:
            pytest.skip("Anthropic smoke credentials/model are not configured")
        transport: ModelTransport = AnthropicTransport(api_key=key)
    elif provider == "openai":
        key = os.getenv("OPENAI_API_KEY")
        model = os.getenv("WINDCODE_OPENAI_SMOKE_MODEL")
        if not key or not model:
            pytest.skip("OpenAI smoke credentials/model are not configured")
        transport = OpenAIResponsesTransport(api_key=key)
    else:
        key = os.getenv("WINDCODE_COMPAT_API_KEY")
        model = os.getenv("WINDCODE_COMPAT_SMOKE_MODEL")
        base_url = os.getenv("WINDCODE_COMPAT_BASE_URL")
        if not key or not model or not base_url:
            pytest.skip("compatible smoke credentials/model/base URL are not configured")
        transport = OpenAICompatibleTransport(api_key=key, base_url=base_url)
    try:
        text = "".join([part async for part in text_events(transport, model)])
        assert "windcode-smoke" in text.casefold()
    finally:
        await transport.aclose()
