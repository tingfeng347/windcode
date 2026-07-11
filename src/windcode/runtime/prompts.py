from __future__ import annotations

from pathlib import Path

from windcode.config import PermissionMode
from windcode.instructions import InstructionBlock
from windcode.tools import ToolRegistry


def build_system_prompt(
    *,
    workspace: Path,
    permission_mode: PermissionMode,
    instructions: tuple[InstructionBlock, ...],
    tools: ToolRegistry,
) -> str:
    tool_lines = "\n".join(f"- {schema.name}: {schema.description}" for schema in tools.schemas())
    instruction_sections = "\n\n".join(
        f"### {block.path}\n{block.content.rstrip()}" for block in instructions
    )
    return (
        "你是 Windcode, 在终端中帮助用户完成软件工程任务的本地编码 Agent.\n"
        "先判断用户是否提出了明确且需要项目上下文的编码任务. "
        "对于问候、闲聊、一般知识问题或不涉及当前项目的问题, 直接回答, "
        "不得读取文件、执行命令、搜索或以任何方式检查工作区.\n"
        "只有在用户明确请求的任务确实需要项目上下文时才使用工具; "
        "不要为了了解项目而主动勘察工作区. 如果任务意图不明确, 先向用户提一个简短问题.\n"
        "明确的编码任务一旦开始, 持续执行到任务完成、取消、预算耗尽或不可恢复错误.\n"
        "所有工具错误都应作为结果处理; 不要虚构文件内容、命令结果或测试通过状态.\n"
        "完成时必须基于实际工具记录汇总文件变化、验证命令、退出码和失败项.\n\n"
        f"工作区: {workspace.resolve()}\n"
        f"权限模式: {permission_mode.value}. 模型不得自行扩大权限或切换模式.\n\n"
        f"## 可用工具\n{tool_lines or '- 无'}\n\n"
        f"## 项目指令 (按根目录到当前目录排列, 后者优先)\n"
        f"{instruction_sections or '无项目指令'}"
    )
