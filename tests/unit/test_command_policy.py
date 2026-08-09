from pathlib import Path

from windcode.policy import ShellDialect, analyze_bash, propose_rule
from windcode.policy.rules import CommandRuleStore


def test_bash_analysis_splits_compound_commands_and_detects_critical() -> None:
    analysis = analyze_bash("git status && rm -rf build")

    assert analysis.trusted
    assert tuple(action.argv for action in analysis.actions) == (
        ("git", "status"),
        ("rm", "-rf", "build"),
    )
    assert analysis.critical


def test_bash_analysis_includes_substitutions_and_redirects() -> None:
    substitution = analyze_bash("echo $(whoami)")
    redirected = analyze_bash("printf x > output.txt")

    assert tuple(action.argv for action in substitution.actions) == (
        ("echo", "$(whoami)"),
        ("whoami",),
    )
    assert redirected.actions[0].redirects == ("output.txt",)


def test_rule_proposal_uses_stable_subcommand_prefix() -> None:
    rule = propose_rule(analyze_bash("git status --short"), network=False, source="project")

    assert rule is not None
    assert rule.dialect is ShellDialect.BASH
    assert rule.argv_prefix == ("git", "status")
    assert not rule.exact


def test_project_rule_store_isolated_by_workspace(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    rule = propose_rule(analyze_bash("git status"), network=False, source="project")
    assert rule is not None

    first_store = CommandRuleStore(tmp_path / "state", first)
    second_store = CommandRuleStore(tmp_path / "state", second)
    first_store.append(rule)

    assert first_store.allows(analyze_bash("git status --short"), network=False)
    assert not second_store.allows(analyze_bash("git status --short"), network=False)
