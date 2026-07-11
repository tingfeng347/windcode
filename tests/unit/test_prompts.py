from pathlib import Path

from windcode.config import PermissionMode
from windcode.instructions import InstructionBlock
from windcode.runtime.prompts import build_system_prompt
from windcode.tools import create_builtin_registry


def test_prompt_orders_instructions_and_requires_actual_verification(tmp_path: Path) -> None:
    root = InstructionBlock(tmp_path / "AGENTS.md", "root rule")
    nested = InstructionBlock(tmp_path / "src" / "WINDCODE.md", "nested rule")

    prompt = build_system_prompt(
        workspace=tmp_path,
        permission_mode=PermissionMode.DEFAULT,
        instructions=(root, nested),
        tools=create_builtin_registry(),
    )

    assert prompt.index("root rule") < prompt.index("nested rule")
    assert "default" in prompt
    assert "read_file" in prompt
    assert "不要虚构" in prompt
    assert "对于问候、闲聊" in prompt
    assert "不得读取文件、执行命令、搜索或以任何方式检查工作区" in prompt
    assert "不要为了了解项目而主动勘察工作区" in prompt
    assert str(tmp_path) in prompt
