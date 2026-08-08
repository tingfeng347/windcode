from __future__ import annotations

from collections.abc import AsyncIterator

from windcode.domain.models import ModelCompleted, ModelEvent, ModelRequest, StopReason, TextDelta
from windcode.memory import MemoryKind, assess_experience, refine_memory
from windcode.providers import ModelTarget


class RefinerTransport:
    name = "refiner"

    def __init__(self, response: str) -> None:
        self.response = response
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield TextDelta(self.response)
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


async def test_model_generates_structured_title_summary_and_body() -> None:
    transport = RefinerTransport(
        '{"title":"编程兴趣","summary":"用户喜欢编程",'
        '"body":"用户将编程视为长期兴趣。","tags":["偏好","编程"]}'
    )
    result = await refine_memory(
        ModelTarget("test", "model", transport),
        text="记住我偏好，我喜欢编程",  # noqa: RUF001
        kind=MemoryKind.USER_PROFILE,
    )
    assert result.title == "编程兴趣"
    assert result.summary == "用户喜欢编程"
    assert result.body == "用户将编程视为长期兴趣。"
    assert result.tags == ("偏好", "编程")
    assert transport.requests[0].tools == ()


async def test_invalid_model_output_uses_deterministic_fallback() -> None:
    transport = RefinerTransport("not json")
    result = await refine_memory(
        ModelTarget("test", "model", transport),
        text="我喜欢编程",
        kind=MemoryKind.USER_PROFILE,
    )
    assert result.summary == "我喜欢编程"
    assert result.body == "我喜欢编程"


async def test_experience_assessment_rejects_routine_or_invalid_results() -> None:
    invalid = RefinerTransport("not json")
    result = await assess_experience(
        ModelTarget("test", "model", invalid),
        text="ruff check 通过",
        evidence=("ruff check (exit 0)",),
    )
    assert not result.should_store

    routine = RefinerTransport('{"should_store":false,"reason":"仅为常规检查"}')
    result = await assess_experience(
        ModelTarget("test", "model", routine),
        text="ruff check 通过",
        evidence=("ruff check (exit 0)",),
    )
    assert not result.should_store
    assert result.reason == "仅为常规检查"


async def test_experience_assessment_requires_reusable_problem_solution() -> None:
    transport = RefinerTransport(
        '{"should_store":true,"reason":"可复用","problem":"FTS 无法分词",'
        '"solution":"增加词法补充检索","applicability":"无空格中文查询",'
        '"title":"中文召回补充","summary":"为中文查询增加二元词补充",'
        '"body":"FTS 无法分词时使用二元词补充并通过测试验证。","tags":["FTS"]}'
    )
    result = await assess_experience(
        ModelTarget("test", "model", transport),
        text="修复中文召回并完成测试",
        evidence=("pytest -q (exit 0)",),
    )
    assert result.should_store
    assert result.memory is not None
    assert result.memory.title == "中文召回补充"
