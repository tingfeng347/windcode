from pathlib import Path

from windcode.config import DelegationMode, PermissionMode
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
    assert "最终面向用户的回复必须使用纯文本" in prompt
    assert "不得输出 Markdown 标记" in prompt
    assert str(tmp_path) in prompt


def test_prompt_describes_explicit_and_proactive_delegation_modes(tmp_path: Path) -> None:
    registry = create_builtin_registry()
    explicit = build_system_prompt(
        workspace=tmp_path,
        permission_mode=PermissionMode.DEFAULT,
        instructions=(),
        tools=registry,
        delegation_mode=DelegationMode.EXPLICIT,
    )
    proactive = build_system_prompt(
        workspace=tmp_path,
        permission_mode=PermissionMode.DEFAULT,
        instructions=(),
        tools=registry,
        delegation_mode=DelegationMode.PROACTIVE,
    )
    child = build_system_prompt(
        workspace=tmp_path,
        permission_mode=PermissionMode.DEFAULT,
        instructions=(),
        tools=registry,
        is_subagent=True,
    )
    assert "用户明确要求" in explicit
    assert "wait_subagents" in explicit
    assert "禁止循环调用 list_subagents" in explicit
    assert "可按运行网络策略和权限审批访问外部网络" in explicit
    assert "主动委派" in proactive
    assert "不得继续委派" in child
    assert "不得直接询问用户" in child


def test_prompt_marks_direct_mcp_servers_as_callable_without_select(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        workspace=tmp_path,
        permission_mode=PermissionMode.DEFAULT,
        instructions=(),
        tools=create_builtin_registry(),
        mcp_direct_servers=("tavily-mcp",),
    )

    assert "工具已直接可用" in prompt
    assert "tavily-mcp" in prompt
    # A directly-exposed server must not be pushed through the select flow.
    assert "select:<id>" not in prompt


def test_prompt_describes_select_flow_only_for_search_servers(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        workspace=tmp_path,
        permission_mode=PermissionMode.DEFAULT,
        instructions=(),
        tools=create_builtin_registry(),
        mcp_search_servers=("lazy-server",),
    )

    assert "按需启用" in prompt
    assert "select:<id>" in prompt
    assert "唯一匹配会自动启用" in prompt
    assert "不得重复搜索" in prompt
    assert "lazy-server" in prompt


def test_prompt_reports_configured_but_unavailable_mcp_servers(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        workspace=tmp_path,
        permission_mode=PermissionMode.DEFAULT,
        instructions=(),
        tools=create_builtin_registry(),
        mcp_unavailable_servers=(("project-server", "workspace is untrusted"),),
    )

    assert "project-server: workspace is untrusted" in prompt
    assert "不得声称 Windcode 没有 MCP 集成" in prompt
    assert "list_mcp_servers" in prompt


def test_prompt_routes_explicit_memory_queries_to_memory_tools(tmp_path: Path) -> None:
    enabled = build_system_prompt(
        workspace=tmp_path,
        permission_mode=PermissionMode.DEFAULT,
        instructions=(),
        tools=create_builtin_registry(),
        memory_enabled=True,
    )
    assert "必须调用 memory_list" in enabled
    assert "不得使用 glob、grep、read_file、shell" in enabled
    assert "自动注入的记忆只用于当前任务上下文" in enabled

    disabled = build_system_prompt(
        workspace=tmp_path,
        permission_mode=PermissionMode.DEFAULT,
        instructions=(),
        tools=create_builtin_registry(),
    )
    assert "长期记忆已禁用或不可用" in disabled
    assert "不得搜索工作区文件" in disabled
