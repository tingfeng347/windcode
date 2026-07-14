from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import aiohttp

from windcode.config import ProviderConfig, ProviderProtocol


def parse_model_ids(payload: object) -> tuple[str, ...]:
    """Extract unique model IDs from OpenAI- and Anthropic-style list responses."""
    if not isinstance(payload, Mapping):
        return ()
    values = cast(Mapping[object, object], payload).get("data")
    if not isinstance(values, list):
        return ()
    model_ids: set[str] = set()
    for value in cast(list[object], values):
        if not isinstance(value, Mapping):
            continue
        model_id = cast(Mapping[object, object], value).get("id")
        if isinstance(model_id, str) and model_id.strip():
            model_ids.add(model_id.strip())
    return tuple(sorted(model_ids, key=str.casefold))


async def fetch_model_ids(
    provider: ProviderConfig,
    api_key: str,
    *,
    timeout_seconds: float = 10.0,
) -> tuple[str, ...]:
    """Load selectable model IDs from a provider's models endpoint."""
    base_url = (provider.base_url or "").rstrip("/")
    if provider.protocol is ProviderProtocol.ANTHROPIC_MESSAGES:
        base_url = base_url or "https://api.anthropic.com"
        url = f"{base_url}/models" if base_url.endswith("/v1") else f"{base_url}/v1/models"
        headers = {
            "anthropic-version": "2023-06-01",
            "x-api-key": api_key,
        }
    else:
        base_url = base_url or "https://api.openai.com/v1"
        url = f"{base_url}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                if response.status >= 400:
                    detail = (await response.text()).strip()
                    if len(detail) > 200:
                        detail = f"{detail[:200]}…"
                    raise RuntimeError(f"加载模型列表失败 ({response.status}): {detail}")
                try:
                    payload: object = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    raise RuntimeError("模型列表响应不是有效 JSON") from exc
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise RuntimeError(f"无法连接 Provider 模型接口: {exc}") from exc
    return parse_model_ids(payload)
