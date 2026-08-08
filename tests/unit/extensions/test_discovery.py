from pathlib import Path

from windcode.extensions.discovery import DiscoveryRoot, discover_skills
from windcode.extensions.models import ActivationState, ExtensionScope


def _skill(root: Path, directory: str, name: str) -> None:
    path = root / directory
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {directory}\n---\nbody")


def test_higher_scope_wins_and_project_trust_controls_activation(tmp_path: Path) -> None:
    user, project = tmp_path / "user", tmp_path / "project"
    _skill(user, "review", "review")
    _skill(project, "review", "review")
    result = discover_skills(
        (
            DiscoveryRoot(project, ExtensionScope.PROJECT, False),
            DiscoveryRoot(user, ExtensionScope.USER),
        )
    )

    user_record, project_record = result.records
    assert user_record.shadowed_by == project_record.source.source_id
    assert project_record.activation is ActivationState.INACTIVE
    assert not project_record.trusted


def test_same_scope_conflict_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _skill(root, "z", "duplicate")
    _skill(root, "a", "duplicate")

    first = discover_skills((DiscoveryRoot(root, ExtensionScope.USER),))
    second = discover_skills((DiscoveryRoot(root, ExtensionScope.USER),))

    assert first == second
    assert len(first.diagnostics) == 2
    assert all(record.activation is ActivationState.FAILED for record in first.records)
