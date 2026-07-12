from __future__ import annotations

import re

_USER_FACT_PATTERNS = (
    re.compile(r"(?:^|[，,。\s])我(?:喜欢|偏好|习惯|希望|通常|总是|不喜欢|不希望).+"),  # noqa: RUF001
    re.compile(r"(?:^|[，,。\s])我的(?:偏好|习惯|常用|工作方式|沟通方式).+(?:是|为).+"),  # noqa: RUF001
    re.compile(r"(?i)(?:^|[,.\s])I\s+(?:like|prefer|usually|always|dislike|want).+"),
    re.compile(r"(?i)(?:^|[,.\s])my\s+(?:preference|workflow|habit).+\s+is\s+.+"),
)
_QUESTION_MARKERS = ("?", "？", "什么", "吗", "么", "why", "what", "how")  # noqa: RUF001


def is_stable_user_fact(text: str) -> bool:
    normalized = " ".join(text.strip().split())
    if not normalized or any(marker in normalized.casefold() for marker in _QUESTION_MARKERS):
        return False
    return any(pattern.search(normalized) is not None for pattern in _USER_FACT_PATTERNS)


def has_explicit_memory_intent(text: str) -> bool:
    normalized = text.casefold()
    return any(marker in normalized for marker in ("记住", "以后都", "remember"))


def is_project_fact(text: str) -> bool:
    normalized = text.casefold()
    return any(
        marker in normalized
        for marker in ("这个项目", "本项目", "仓库", "代码库", "this project", "repository")
    )
