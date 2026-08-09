from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import cast

SOURCE_ROOT = Path("src/windcode")
KEY_FUNCTIONS = frozenset(
    {
        "windcode.sdk:Windcode.start_run",
        "windcode.runtime.loop:AgentLoop.run",
        "windcode.runtime.run_builder:RunBuilder.prepare_child",
        "windcode.tui.app:WindcodeApp._command",
    }
)


def _module_name(path: Path) -> str:
    relative = path.relative_to(SOURCE_ROOT.parent).with_suffix("")
    parts = relative.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_import(module: str, alias: str, modules: set[str]) -> str | None:
    candidate = f"{module}.{alias}"
    if candidate in modules:
        return candidate
    while module and module not in modules:
        module = module.rpartition(".")[0]
    return module or None


def _dependencies(tree: ast.AST, modules: set[str]) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("windcode"):
                    resolved = _resolve_import(alias.name, "", modules)
                    if resolved is not None:
                        result.add(resolved)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if not node.module.startswith("windcode"):
                continue
            for alias in node.names:
                resolved = _resolve_import(node.module, alias.name, modules)
                if resolved is not None:
                    result.add(resolved)
    return result


def _strongly_connected(graph: Mapping[str, set[str]]) -> tuple[tuple[str, ...], ...]:
    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    groups: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph.get(node, set()):
            if target not in indexes:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[target])
        if lowlinks[node] != indexes[node]:
            return
        group: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            group.append(member)
            if member == node:
                break
        if len(group) > 1:
            groups.append(tuple(sorted(group)))

    for node in sorted(graph):
        if node not in indexes:
            visit(node)
    return tuple(sorted(groups))


class _Complexity(ast.NodeVisitor):
    def __init__(self) -> None:
        self.value = 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        del node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        del node

    def visit_If(self, node: ast.If) -> None:
        self.value += 1
        self.generic_visit(node)

    visit_For = visit_If
    visit_AsyncFor = visit_If
    visit_While = visit_If
    visit_IfExp = visit_If

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.value += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self.value += len(node.handlers) + bool(node.orelse)
        self.generic_visit(node)

    visit_TryStar = visit_Try

    def visit_Match(self, node: ast.Match) -> None:
        self.value += len(node.cases)
        self.generic_visit(node)


def _function_metrics(module: str, tree: ast.Module) -> tuple[dict[str, int], list[int]]:
    complexities: dict[str, int] = {}
    constructors: list[int] = []

    def walk(body: Iterable[ast.stmt], prefix: tuple[str, ...] = ()) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                walk(node.body, (*prefix, node.name))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{module}:{'.'.join((*prefix, node.name))}"
                visitor = _Complexity()
                for statement in node.body:
                    visitor.visit(statement)
                complexities[name] = visitor.value
                if node.name == "__init__":
                    positional = len(node.args.posonlyargs) + len(node.args.args)
                    if positional and node.args.args and node.args.args[0].arg in {"self", "cls"}:
                        positional -= 1
                    constructors.append(
                        positional
                        + len(node.args.kwonlyargs)
                        + int(node.args.vararg is not None)
                        + int(node.args.kwarg is not None)
                    )
                walk(node.body, (*prefix, node.name))

    walk(tree.body)
    return complexities, constructors


def collect() -> dict[str, object]:
    paths = sorted(SOURCE_ROOT.rglob("*.py"))
    modules = {_module_name(path) for path in paths}
    graph: dict[str, set[str]] = {}
    complexities: dict[str, int] = {}
    constructors: list[int] = []
    for path in paths:
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        graph[module] = _dependencies(tree, modules) - {module}
        module_complexities, module_constructors = _function_metrics(module, tree)
        complexities.update(module_complexities)
        constructors.extend(module_constructors)

    component_graph: dict[str, set[str]] = defaultdict(set)
    for source, targets in graph.items():
        source_component = source.split(".", 2)[1] if "." in source else source
        for target in targets:
            target_component = target.split(".", 2)[1] if "." in target else target
            if source_component != target_component:
                component_graph[source_component].add(target_component)
    for component in {module.split(".", 2)[1] for module in modules if "." in module}:
        component_graph.setdefault(component, set())

    module_scc = _strongly_connected(graph)
    component_scc = _strongly_connected(component_graph)
    tools_to_runtime_edges = tuple(
        sorted(
            f"{source} -> {target}"
            for source, targets in graph.items()
            for target in targets
            if source.startswith("windcode.tools") and target.startswith("windcode.runtime")
        )
    )
    extensions_to_runtime_edges = tuple(
        sorted(
            f"{source} -> {target}"
            for source, targets in graph.items()
            for target in targets
            if source.startswith("windcode.extensions") and target.startswith("windcode.runtime")
        )
    )
    return {
        "module_count": len(modules),
        "component_scc_count": len(component_scc),
        "max_component_scc_size": max((len(group) for group in component_scc), default=0),
        "module_scc_count": len(module_scc),
        "max_module_scc_size": max((len(group) for group in module_scc), default=0),
        "modules_with_fanout_over_20": sum(len(targets) > 20 for targets in graph.values()),
        "max_constructor_parameters": max(constructors, default=0),
        "tools_to_runtime_edges": len(tools_to_runtime_edges),
        "tools_to_runtime_edge_list": tools_to_runtime_edges,
        "extensions_to_runtime_edges": len(extensions_to_runtime_edges),
        "extensions_to_runtime_edge_list": extensions_to_runtime_edges,
        "key_function_complexity": {
            name: complexities[name] for name in sorted(KEY_FUNCTIONS) if name in complexities
        },
        "component_sccs": component_scc,
        "module_sccs": module_scc,
    }


def check_against_baseline(
    actual: Mapping[str, object], baseline: Mapping[str, object]
) -> list[str]:
    failures: list[str] = []
    ceilings = cast(Mapping[str, object], baseline.get("ceilings", {}))
    for name, expected in ceilings.items():
        value = actual.get(name)
        if isinstance(value, int) and isinstance(expected, int) and value > expected:
            failures.append(f"{name}: {value} exceeds {expected}")
    expected_complexity = cast(Mapping[str, object], baseline.get("key_function_complexity", {}))
    actual_complexity = cast(Mapping[str, object], actual["key_function_complexity"])
    for name, expected in expected_complexity.items():
        value = actual_complexity.get(name)
        if value is None:
            failures.append(f"{name}: tracked function is missing")
        elif isinstance(value, int) and isinstance(expected, int) and value > expected:
            failures.append(f"{name}: complexity {value} exceeds {expected}")
    allowed_scc_members = cast(Mapping[str, object], baseline.get("allowed_scc_members", {}))
    for level in ("component", "module"):
        groups = cast(tuple[tuple[str, ...], ...], actual[f"{level}_sccs"])
        members = {member for group in groups for member in group}
        allowed = set(cast(list[str], allowed_scc_members.get(level, [])))
        if unexpected := sorted(members - allowed):
            failures.append(f"{level} SCC has new members: {', '.join(unexpected)}")
    allowed_edges = cast(Mapping[str, object], baseline.get("allowed_edges", {}))
    for direction in ("tools_to_runtime", "extensions_to_runtime"):
        edges = set(cast(tuple[str, ...], actual[f"{direction}_edge_list"]))
        allowed = set(cast(list[str], allowed_edges.get(direction, [])))
        if unexpected := sorted(edges - allowed):
            failures.append(f"{direction} has new edges: {', '.join(unexpected)}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure Windcode architecture dependencies")
    parser.add_argument("--check", type=Path, metavar="BASELINE")
    options = parser.parse_args()
    metrics = collect()
    print(json.dumps(metrics, indent=2, sort_keys=True))
    if options.check is None:
        return 0
    baseline = cast(dict[str, object], json.loads(options.check.read_text(encoding="utf-8")))
    failures = check_against_baseline(metrics, baseline)
    for failure in failures:
        print(f"architecture regression: {failure}")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
