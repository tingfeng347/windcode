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


@dataclass(frozen=True, slots=True)
class ExperienceAssessment:
    should_store: bool
    reason: str = ""
    memory: RefinedMemory | None = None


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


async def assess_experience(
    target: ModelTarget,
    *,
    text: str,
    evidence: tuple[str, ...],
    max_output_tokens: int = 600,
) -> ExperienceAssessment:
    """Accept only reusable problem-solving knowledge; malformed output means no memory."""
    evidence_text = "\n".join(f"- {item}" for item in evidence)
    prompt = (
        "判断本次任务是否形成值得长期保存的可复用工程经验。只返回 JSON, 不要 Markdown。\n"
        "普通检查通过、测试通过、查看文件、重复既有流程都不属于经验。\n"
        "只有同时具备明确问题、实际解决方法、成功验证和可再次识别的适用条件时, "
        "should_store 才能为 true。\n"
        '{"should_store":false,"reason":"原因","problem":"问题",'
        '"solution":"解决方法","applicability":"适用条件","title":"标题",'
        '"summary":"摘要","body":"问题、方法、验证与适用范围","tags":["标签"]}\n'
        f"验证证据:\n{evidence_text}\n任务结果:\n{text}"
    )
    request = ModelRequest(
        model=target.model,
        messages=(Message(Role.USER, (TextBlock(prompt),)),),
        system_prompt="你是保守的工程经验筛选器。拿不准时必须拒绝保存。",
        max_output_tokens=max_output_tokens,
    )
    parts: list[str] = []
    try:
        async for event in target.transport.stream(request):
            if isinstance(event, TextDelta):
                parts.append(event.text)
        raw = json.loads("".join(parts).strip())
    except Exception:
        return ExperienceAssessment(False, "模型评估失败")
    if not isinstance(raw, dict):
        return ExperienceAssessment(False, "模型评估格式无效")
    value = cast(dict[str, Any], raw)
    reason = str(value.get("reason", "")).strip()
    required = tuple(
        str(value.get(key, "")).strip()
        for key in ("problem", "solution", "applicability", "title", "summary", "body")
    )
    if value.get("should_store") is not True or not all(required):
        return ExperienceAssessment(False, reason or "缺少可复用的问题解决信息")
    fallback = _fallback(text, MemoryKind.EXPERIENCE)
    memory = _decode(json.dumps(value, ensure_ascii=False), fallback)
    return ExperienceAssessment(True, reason, memory)
