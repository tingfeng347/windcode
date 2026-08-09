import json
from inspect import signature
from pathlib import Path
from typing import cast

from scripts.architecture_metrics import check_against_baseline, collect
from windcode.runtime.loop import AgentLoop

BASELINE_PATH = Path("docs/refactor/architecture-baseline.json")


def _baseline() -> dict[str, object]:
    return cast(dict[str, object], json.loads(BASELINE_PATH.read_text(encoding="utf-8")))


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
