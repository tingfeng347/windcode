from windcode.context import TokenEstimator
from windcode.domain.messages import Message, Role, TextBlock
from windcode.domain.models import ModelRequest, Usage


def test_uses_actual_usage_as_anchor_and_triggers_at_boundary() -> None:
    estimator = TokenEstimator(10_000, compaction_threshold=0.8, reserved_output_tokens=1_000)
    request = ModelRequest(model="model", messages=(), system_prompt="")

    before = estimator.estimate(request, actual_usage=Usage(input_tokens=7_999))
    at = estimator.estimate(request, actual_usage=Usage(input_tokens=8_000))

    assert not before.should_compact
    assert at.should_compact
    assert at.remaining_tokens == 1_000


def test_estimates_messages_without_usage() -> None:
    request = ModelRequest(
        model="model",
        messages=(Message(Role.USER, (TextBlock("x" * 400),)),),
        system_prompt="system",
    )
    budget = TokenEstimator(10_000, reserved_output_tokens=1_000).estimate(request)
    assert budget.estimated_tokens >= 100
