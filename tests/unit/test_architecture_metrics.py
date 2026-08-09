import ast
import json
from inspect import signature
from pathlib import Path
from typing import cast

from scripts.architecture_metrics import check_against_baseline, collect
from windcode.runtime.loop import AgentLoop
from windcode.runtime.run_builder import RunBuilder

BASELINE_PATH = Path("docs/refactor/architecture-baseline.json")
ASSEMBLY_TARGETS = {
    "AgentLoop",
    "ChildAgentLoop",
    "RunResources.create",
}
ASSEMBLY_ALIAS_TARGETS = {*ASSEMBLY_TARGETS, "RunResources"}


def _baseline() -> dict[str, object]:
    return cast(dict[str, object], json.loads(BASELINE_PATH.read_text(encoding="utf-8")))


def _qualified_name(node: ast.expr, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        owner = _qualified_name(node.value, aliases)
        return None if owner is None else f"{owner}.{node.attr}"
    return None


def _assembly_calls(tree: ast.Module) -> set[str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                local = item.asname or item.name.split(".", 1)[0]
                aliases[local] = item.name if item.asname else local
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"

    # Resolve simple local aliases such as `Loop = AgentLoop` before inspecting calls.
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            value = _qualified_name(node.value, aliases) if node.value is not None else None
            if value is None or not any(
                value == target or value.endswith(f".{target}") for target in ASSEMBLY_ALIAS_TARGETS
            ):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases[target.id] = value
                    changed = True

    calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        qualified = _qualified_name(node.func, aliases)
        if qualified is None:
            continue
        for target in ASSEMBLY_TARGETS:
            if qualified == target or qualified.endswith(f".{target}"):
                calls.add(target)
    return calls


def _run_builder_calls(tree: ast.Module) -> set[str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                local = item.asname or item.name.split(".", 1)[0]
                aliases[local] = item.name if item.asname else local
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"

    calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        qualified = _qualified_name(node.func, aliases)
        if qualified == "RunBuilder" or (
            qualified is not None and qualified.endswith(".RunBuilder")
        ):
            calls.add("RunBuilder")
    return calls


def test_current_architecture_matches_baseline() -> None:
    assert check_against_baseline(collect(), _baseline()) == []


def test_runtime_boundaries_have_no_cycles_or_reverse_dependencies() -> None:
    actual = collect()

    assert actual["component_sccs"] == ()
    assert actual["module_sccs"] == ()
    assert actual["tools_to_runtime_edge_list"] == ()
    assert actual["extensions_to_runtime_edge_list"] == ()


def test_agent_loop_constructor_stays_narrow() -> None:
    assert len(signature(AgentLoop).parameters) <= 10


def test_run_builder_interface_stays_narrow() -> None:
    assert len(signature(RunBuilder).parameters) <= 7
    assert len(signature(RunBuilder.start).parameters) == 2


def test_parent_and_child_share_run_builder_assembly_boundary() -> None:
    constructor_sites = {target: set[str]() for target in ASSEMBLY_TARGETS}
    runtime_root = Path("src/windcode/runtime")
    for path in runtime_root.rglob("*.py"):
        module = ".".join(path.with_suffix("").parts[1:])
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for target in _assembly_calls(tree):
            constructor_sites[target].add(module)

    assert constructor_sites == {
        "AgentLoop": {"windcode.runtime.run_builder"},
        "ChildAgentLoop": {"windcode.runtime.run_builder"},
        "RunResources.create": {"windcode.runtime.run_builder"},
    }
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8") for path in runtime_root.rglob("*.py")
    )
    assert "ChildRunScope" not in runtime_source


def test_sdk_delegates_parent_run_ownership_to_run_application() -> None:
    constructor_sites: set[str] = set()
    for path in Path("src/windcode").rglob("*.py"):
        module = ".".join(path.with_suffix("").parts[1:])
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _run_builder_calls(tree):
            constructor_sites.add(module)

    sdk_tree = ast.parse(Path("src/windcode/sdk.py").read_text(encoding="utf-8"))
    sdk_attributes = {node.attr for node in ast.walk(sdk_tree) if isinstance(node, ast.Attribute)}
    sdk_methods = {
        node.name
        for node in ast.walk(sdk_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert constructor_sites == {"windcode.application.runs"}
    assert "_handles" not in sdk_attributes
    assert {"_run_builder", "_accepting_runs"}.isdisjoint(sdk_methods)


def test_sdk_delegates_cross_module_lifecycle_to_application() -> None:
    sdk_tree = ast.parse(Path("src/windcode/sdk.py").read_text(encoding="utf-8"))
    sdk_attributes = {node.attr for node in ast.walk(sdk_tree) if isinstance(node, ast.Attribute)}

    assert {"_lifecycle_lock", "_entered", "_closing"}.isdisjoint(sdk_attributes)


def test_sdk_depends_only_on_public_facades_and_contracts() -> None:
    sdk_tree = ast.parse(Path("src/windcode/sdk.py").read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(sdk_tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("windcode")
    }

    assert len(imported_modules) <= 5
    assert not any(
        module.startswith(("windcode.extensions", "windcode.providers", "windcode.runtime"))
        for module in imported_modules
    )


def test_assembly_boundary_resolves_import_and_assignment_aliases() -> None:
    tree = ast.parse(
        """
from windcode.runtime.loop import AgentLoop as Loop
import windcode.runtime.resources as resources
from windcode.runtime.subagents.child_execution import ChildAgentLoop as Child

IndirectLoop = Loop
Resources = resources.RunResources
IndirectLoop()
Child()
Resources.create()
"""
    )

    assert _assembly_calls(tree) == ASSEMBLY_TARGETS


def test_architecture_check_rejects_missing_tracked_function() -> None:
    actual = collect()
    complexities = dict(cast(dict[str, object], actual["key_function_complexity"]))
    complexities.pop("windcode.sdk:Windcode.start_run")
    actual["key_function_complexity"] = complexities

    failures = check_against_baseline(actual, _baseline())

    assert any("tracked function is missing" in failure for failure in failures)


def test_architecture_check_rejects_new_reverse_dependency() -> None:
    actual = collect()
    edges = cast(tuple[str, ...], actual["tools_to_runtime_edge_list"])
    actual["tools_to_runtime_edge_list"] = (
        *edges,
        "windcode.tools.new_tool -> windcode.runtime.new_service",
    )

    failures = check_against_baseline(actual, _baseline())

    assert any("tools_to_runtime has new edges" in failure for failure in failures)
