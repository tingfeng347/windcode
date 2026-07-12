from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from windcode.config import DelegationMode, PermissionMode
from windcode.instructions import InstructionBlock
from windcode.tools import ToolRegistry

if TYPE_CHECKING:
    from windcode.extensions.skills.tools import SkillSearchResult


def build_system_prompt(
    *,
    workspace: Path,
    permission_mode: PermissionMode,
    instructions: tuple[InstructionBlock, ...],
    tools: ToolRegistry,
    delegation_mode: DelegationMode | None = None,
    is_subagent: bool = False,
    skills: tuple[SkillSearchResult, ...] = (),
    mcp_direct_servers: tuple[str, ...] = (),
    mcp_search_servers: tuple[str, ...] = (),
    mcp_unavailable_servers: tuple[tuple[str, str], ...] = (),
    memory_enabled: bool = False,
) -> str:
    tool_lines = "\n".join(f"- {schema.name}: {schema.description}" for schema in tools.schemas())
    instruction_sections = "\n\n".join(
        f"### {block.path}\n{block.content.rstrip()}" for block in instructions
    )
    extension_sections = ""
    if skills:
        skill_lines = "\n".join(
            f"- ${item.name}: {item.description} [source: {item.source_id}]" for item in skills
        )
        extension_sections += (
            f"\n\n## Agent Skills\n{skill_lines}\n"
            "需要某个明确匹配的 Skill 时直接调用 load_skill, 不确定时最多调用一次 "
            "search_skills 后选择准确名称加载。不得尝试加载列表之外、未启用或未信任的 Skill; "
            "同一 Skill 不得重复加载。用户以 $name 显式选择的 Skill 已由运行时加载, "
            "无需再次调用 load_skill。"
        )
    if mcp_direct_servers:
        server_lines = "\n".join(f"- {server_id}" for server_id in sorted(mcp_direct_servers))
        extension_sections += (
            f"\n\n## MCP 服务器 (工具已直接可用)\n{server_lines}\n"
            "这些服务器的工具已列在上方“可用工具”中, 直接按名称调用即可, "
            "无需再调用 search_mcp_tools 搜索或启用。"
        )
    if mcp_search_servers:
        server_lines = "\n".join(f"- {server_id}" for server_id in sorted(mcp_search_servers))
        extension_sections += (
            f"\n\n## MCP 服务器 (按需启用)\n{server_lines}\n"
            "先检查上方可用工具; 已存在目标 MCP 工具时必须直接调用, 不得重复搜索。"
            "目标尚不可用时调用一次 search_mcp_tools 并传关键词; 唯一匹配会自动启用并返回 "
            "call_name, 随后直接调用它。只有返回多个匹配时, 才再调用一次 "
            "search_mcp_tools(query='select:<id>') 选择目标。已启用工具会在后续运行中复用。"
        )
    if mcp_unavailable_servers:
        server_lines = "\n".join(
            f"- {server_id}: {reason}" for server_id, reason in sorted(mcp_unavailable_servers)
        )
        extension_sections += (
            f"\n\n## MCP 服务器 (本次运行不可用)\n{server_lines}\n"
            "这些服务器已经配置, 因此不得声称 Windcode 没有 MCP 集成。"
            "它们当前不能调用; 可使用 list_mcp_servers 核实状态, 并向用户准确说明原因。"
        )
    delegation = ""
    if is_subagent:
        delegation = (
            "\n\n## 子智能体约束\n"
            "你是单层临时子智能体, 只执行收到的自包含任务; 不得继续委派, 也不得直接询问用户。"
        )
    elif delegation_mode is DelegationMode.EXPLICIT:
        delegation = (
            "\n\n## 委派策略: explicit\n"
            "仅当用户明确要求委派、并行或使用子智能体时, 才可调用子智能体工具。"
            "创建后调用 wait_subagents 一次等待结果; 禁止循环调用 list_subagents。"
            "子智能体可按运行网络策略和权限审批访问外部网络。"
        )
    elif delegation_mode is DelegationMode.PROACTIVE:
        delegation = (
            "\n\n## 委派策略: proactive\n"
            "可在任务确实独立且适合并行时主动委派; 必须保持任务有界、状态可见并统一汇总。"
            "创建后调用 wait_subagents 一次等待结果; 禁止循环调用 list_subagents。"
            "子智能体可按运行网络策略和权限审批访问外部网络。"
        )
    if memory_enabled:
        memory_policy = (
            "\n\n## 长期记忆主动查询\n"
            "当用户明确要求查看、列出、搜索、核对或回忆长期记忆时, 必须调用 memory_list、"
            "memory_search 或 memory_get, 并基于工具实际结果回答。宽泛的‘看看长期记忆’调用 "  # noqa: RUF001
            "memory_list; 带主题的请求调用 memory_search; 查看单条详情调用 memory_get。"
            "不得使用 glob、grep、read_file、shell 或其他工作区工具代替长期记忆查询。"
            "自动注入的记忆只用于当前任务上下文, 不能冒充主动查询结果。"
            "搜索无结果时直接说明没有匹配记忆, 不得转而扫描仓库。"
        )
    else:
        memory_policy = (
            "\n\n## 长期记忆主动查询\n"
            "本次运行未启用长期记忆工具。用户要求查看长期记忆时应准确说明长期记忆已禁用或"
            "不可用, 不得搜索工作区文件来代替记忆查询。"
        )
    return (
        "你是 Windcode, 在终端中帮助用户完成软件工程任务的本地编码 Agent.\n"
        "最终面向用户的回复可使用 GitHub 风格 Markdown。仅在有助于阅读时使用标题、"
        "列表、表格、引用、强调、行内代码和围栏代码块; 保持结构克制且内容简洁。\n"
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
        f"{extension_sections}"
        f"{memory_policy}"
        f"{delegation}"
    )
