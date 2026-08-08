from windcode.domain.messages import (
    Message,
    Role,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    heal_dangling_tool_calls,
)


def _assistant_call(call_id: str, name: str = "search_mcp_tools") -> Message:
    return Message(Role.ASSISTANT, (ToolCallBlock(call_id, name, {}),))


def test_heal_appends_result_for_unanswered_tool_call() -> None:
    messages = (
        Message(Role.USER, (TextBlock("今天杭州的天气如何"),)),
        _assistant_call("call-1"),
    )

    healed = heal_dangling_tool_calls(messages)

    assert len(healed) == 3
    tool_message = healed[-1]
    assert tool_message.role is Role.TOOL
    block = tool_message.content[0]
    assert isinstance(block, ToolResultBlock)
    assert block.call_id == "call-1"
    assert block.is_error


def test_heal_leaves_answered_tool_calls_untouched() -> None:
    messages = (
        _assistant_call("call-1"),
        Message(Role.TOOL, (ToolResultBlock("call-1", "search_mcp_tools", "ok"),)),
    )

    assert heal_dangling_tool_calls(messages) == messages


def test_heal_fills_only_missing_call_ids() -> None:
    messages = (
        Message(
            Role.ASSISTANT,
            (ToolCallBlock("call-1", "a", {}), ToolCallBlock("call-2", "b", {})),
        ),
        Message(Role.TOOL, (ToolResultBlock("call-1", "a", "done"),)),
    )

    healed = heal_dangling_tool_calls(messages)

    assert len(healed) == 3
    supplemental = healed[-1]
    assert supplemental.role is Role.TOOL
    call_ids = [
        block.call_id for block in supplemental.content if isinstance(block, ToolResultBlock)
    ]
    assert call_ids == ["call-2"]


def test_heal_ignores_plain_assistant_messages() -> None:
    messages = (
        Message(Role.USER, (TextBlock("hi"),)),
        Message(Role.ASSISTANT, (TextBlock("hello"),)),
    )

    assert heal_dangling_tool_calls(messages) == messages


def test_heal_discards_orphan_tool_results() -> None:
    messages = (
        Message(Role.USER, (TextBlock("hi"),)),
        Message(Role.TOOL, (ToolResultBlock("orphan", "search", "result"),)),
        Message(Role.ASSISTANT, (TextBlock("done"),)),
    )

    assert heal_dangling_tool_calls(messages) == (messages[0], messages[2])


def test_heal_discards_mismatched_and_duplicate_tool_results() -> None:
    call = _assistant_call("call-1")
    first = Message(Role.TOOL, (ToolResultBlock("call-1", "search", "first"),))
    messages = (
        call,
        Message(Role.TOOL, (ToolResultBlock("other", "search", "wrong"),)),
        first,
        Message(Role.TOOL, (ToolResultBlock("call-1", "search", "duplicate"),)),
    )

    assert heal_dangling_tool_calls(messages) == (call, first)
