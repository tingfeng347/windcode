from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from windcode.domain.messages import Message, Role, TextBlock
from windcode.domain.models import ModelRequest, TextDelta
from windcode.memory.models import MemoryKind
from windcode.providers import ModelTarget


@dataclass(frozen=True, slots=True)
class RefinedMemory:
    title: str
    summary: str
    body: str
    tags: tuple[str, ...]


def _fallback(text: str, kind: MemoryKind) -> RefinedMemory:
    compact = " ".join(text.split()).strip()
    title = compact if len(compact) <= 40 else compact[:37].rstrip() + "..."
    summary = compact if len(compact) <= 120 else compact[:117].rstrip() + "..."
    return RefinedMemory(title or kind.value, summary, text.strip(), ())


def _decode(text: str, fallback: RefinedMemory) -> RefinedMemory:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        candidate = "\n".join(lines[1:-1]).strip()
    try:
        raw = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return fallback
    if not isinstance(raw, dict):
        return fallback
    value = cast(dict[str, Any], raw)
    title = str(value.get("title", "")).strip()
    summary = str(value.get("summary", "")).strip()
    body = str(value.get("body", "")).strip()
    raw_tags = value.get("tags", ())
    tags = (
        tuple(str(item).strip() for item in cast(list[object], raw_tags) if str(item).strip())[:8]
        if isinstance(raw_tags, list)
        else ()
    )
    if not title or not summary or not body:
        return fallback
    return RefinedMemory(title[:80], summary[:240], body[:4_000], tags)


async def refine_memory(
    target: ModelTarget,
    *,
    text: str,
    kind: MemoryKind,
    evidence: tuple[str, ...] = (),
    max_output_tokens: int = 600,
) -> RefinedMemory:
    """Use a tool-free side request to normalize a validated memory candidate."""
    fallback = _fallback(text, kind)
    evidence_text = "\n".join(f"- {item}" for item in evidence) or "无"
    prompt = (
        "将下面的信息提炼为高密度长期记忆。只返回 JSON 对象，不要 Markdown。\n"  # noqa: RUF001
        '格式: {"title":"简短主题","summary":"一句规范事实",'
        '"body":"保留必要上下文的规范正文","tags":["标签"]}\n'
        "不得添加原文没有的事实，不得把推测写成结论，不得包含密钥或凭据。\n"  # noqa: RUF001
        f"记忆类型: {kind.value}\n验证证据:\n{evidence_text}\n原始内容:\n{text}"
    )
    request = ModelRequest(
        model=target.model,
        messages=(Message(Role.USER, (TextBlock(prompt),)),),
        system_prompt="你是 Windcode 的长期记忆提炼器。只输出严格 JSON，不调用工具。",  # noqa: RUF001
        max_output_tokens=max_output_tokens,
    )
    parts: list[str] = []
    try:
        async for event in target.transport.stream(request):
            if isinstance(event, TextDelta):
                parts.append(event.text)
    except Exception:
        return fallback
    return _decode("".join(parts), fallback)
