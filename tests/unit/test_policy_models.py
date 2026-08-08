import pytest
from pydantic import ValidationError

from windcode.domain.tools import ToolEffect
from windcode.policy import ApprovalChoice, PolicyRequest, summarize_policy_arguments


def test_policy_request_serializes_effects_without_sensitive_arguments() -> None:
    request = PolicyRequest(
        request_id="request",
        call_id="call",
        tool_name="write_file",
        effects=frozenset({ToolEffect.WORKSPACE_WRITE}),
        summary="Write README.md",
        path="README.md",
    )
    assert request.model_dump(mode="json")["effects"] == ["workspace_write"]
    assert "arguments" not in request.model_dump()


def test_approval_choice_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        ApprovalChoice("always")
    with pytest.raises(ValidationError):
        PolicyRequest.model_validate({"request_id": "missing fields"})


def test_policy_argument_summary_is_single_line_and_bounded() -> None:
    request = PolicyRequest(
        request_id="request",
        call_id="call",
        tool_name="shell",
        effects=frozenset({ToolEffect.PROCESS}),
        summary="run shell",
        command="printf 'one'\n  && printf 'two'",
    )

    assert summarize_policy_arguments(request) == "printf 'one' && printf 'two'"
    assert summarize_policy_arguments(request, limit=12) == "printf 'o..."
