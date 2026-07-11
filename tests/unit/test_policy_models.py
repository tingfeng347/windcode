import pytest
from pydantic import ValidationError

from windcode.domain.tools import ToolEffect
from windcode.policy import ApprovalChoice, PolicyRequest


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
