from __future__ import annotations

import re

from windcode.memory.models import MemoryKind

_USER_FACT_PATTERNS = (
    re.compile(r"(?:^|[，,。\s])我(?:喜欢|偏好|习惯|希望|通常|总是|不喜欢|不希望).+"),  # noqa: RUF001
    re.compile(r"(?:^|[，,。\s])我的(?:偏好|习惯|常用|工作方式|沟通方式).+(?:是|为).+"),  # noqa: RUF001
    re.compile(r"(?i)(?:^|[,.\s])I\s+(?:like|prefer|usually|always|dislike|want).+"),
    re.compile(r"(?i)(?:^|[,.\s])my\s+(?:preference|workflow|habit).+\s+is\s+.+"),
)
_QUESTION_MARKERS = ("?", "？", "什么", "吗", "么", "why", "what", "how")  # noqa: RUF001
_EXPERIENCE_MARKERS = ("经验", "lesson")
_REFERENCE_MARKERS = ("参考资料", "这份资料", "以下资料", "reference")
_SOP_MARKERS = ("sop", "标准操作流程", "操作规程", "执行流程", "工作流程")
_ALWAYS_PROJECT_MARKERS = ("每次都要记住", "始终适用", "永远适用", "always applies")


def is_stable_user_fact(text: str) -> bool:
    normalized = " ".join(text.strip().split())
    if not normalized or any(marker in normalized.casefold() for marker in _QUESTION_MARKERS):
        return False
    return any(pattern.search(normalized) is not None for pattern in _USER_FACT_PATTERNS)


def has_explicit_memory_intent(text: str) -> bool:
    normalized = text.casefold()
    return any(
        marker in normalized
        for marker in (
            "记住",
            "记下来",
            "记录下来",
            "以后都",
            "写入长期记忆",
            "加入长期记忆",
            "remember",
        )
    )


def is_project_fact(text: str) -> bool:
    normalized = text.casefold()
    return any(
        marker in normalized
        for marker in ("这个项目", "本项目", "仓库", "代码库", "this project", "repository")
    )


def classify_memory_intent(text: str) -> MemoryKind | None:
    """Classify explicit memory requests before applying stable-fact heuristics."""
    normalized = " ".join(text.strip().split()).casefold()
    if has_explicit_memory_intent(normalized):
        if any(marker in normalized for marker in _EXPERIENCE_MARKERS):
            return MemoryKind.EXPERIENCE
        if any(marker in normalized for marker in _SOP_MARKERS):
            return MemoryKind.SOP
        if any(marker in normalized for marker in _REFERENCE_MARKERS):
            return MemoryKind.REFERENCE
        if is_project_fact(normalized):
            return MemoryKind.PROJECT_KNOWLEDGE
        return MemoryKind.USER_PROFILE
    if is_stable_user_fact(normalized):
        return MemoryKind.USER_PROFILE
    return None


def explicitly_always_project_fact(text: str) -> bool:
    normalized = " ".join(text.strip().split()).casefold()
    return is_project_fact(normalized) and any(
        marker in normalized for marker in _ALWAYS_PROJECT_MARKERS
    )


def should_assess_experience(
    *,
    status: str,
    changed_files: tuple[str, ...],
    verification: tuple[str, ...],
) -> bool:
    """Require a successful code change before spending a model call on experience extraction."""
    return status == "completed" and bool(changed_files) and bool(verification)
