from __future__ import annotations

from dataclasses import dataclass

from windcode.domain.messages import Message, Role, TextBlock, heal_dangling_tool_calls
from windcode.domain.models import ModelRequest, TextDelta
from windcode.providers.base import ModelTransport

CHECKPOINT_SECTIONS = (
    "任务目标",
    "关键决策",
    "相关文件",
    "当前进度",
    "未完成事项",
    "验证证据",
    "约束与指令",
    "下一步",
)


@dataclass(frozen=True, slots=True)
class CompactionResult:
    messages: tuple[Message, ...]
    checkpoint: str | None
    error: str | None = None

    @property
    def compacted(self) -> bool:
        return self.checkpoint is not None


def checkpoint_prompt() -> str:
    sections = "\n".join(f"## {section}\n[简明, 具体, 无遗漏]" for section in CHECKPOINT_SECTIONS)
    return f"请根据完整对话生成结构化检查点. 必须准确记录已验证证据, 不要推测成功.\n\n{sections}"


def _valid_checkpoint(text: str) -> bool:
    return all(f"## {section}" in text for section in CHECKPOINT_SECTIONS)


async def compact_context(
    messages: tuple[Message, ...],
    transport: ModelTransport,
    *,
    model: str,
    system_prompt: str,
    preserve_recent_turns: int = 8,
) -> CompactionResult:
    normalized = heal_dangling_tool_calls(messages)
    request = ModelRequest(
        model=model,
        messages=(*normalized, Message(Role.USER, (TextBlock(checkpoint_prompt()),))),
        system_prompt=system_prompt,
    )
    parts: list[str] = []
    try:
        async for event in transport.stream(request):
            if isinstance(event, TextDelta):
                parts.append(event.text)
    except Exception as exc:
        return CompactionResult(messages, None, f"checkpoint generation failed: {exc}")
    checkpoint = "".join(parts).strip()
    if not _valid_checkpoint(checkpoint):
        return CompactionResult(messages, None, "checkpoint response is missing required sections")

    system_messages = tuple(message for message in normalized if message.role is Role.SYSTEM)
    non_system = tuple(message for message in normalized if message.role is not Role.SYSTEM)
    recent = non_system[-preserve_recent_turns * 2 :]
    checkpoint_message = Message(
        Role.SYSTEM,
        (TextBlock(f"上下文检查点:\n{checkpoint}"),),
    )
    compacted = (*system_messages, checkpoint_message, *recent)
    return CompactionResult(heal_dangling_tool_calls(compacted), checkpoint)
