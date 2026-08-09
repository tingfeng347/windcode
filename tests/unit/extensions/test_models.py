from pathlib import Path

import pytest

from windcode.extensions.models import (
    CapabilityKind,
    CapabilityRecord,
    ExtensionScope,
    ExtensionSnapshot,
    ExtensionSource,
    capability_id,
    normalize_id,
)


def test_capability_ids_are_normalized_and_namespaced() -> None:
    assert capability_id(CapabilityKind.SKILL, "Code Review") == "skill:code-review"
    assert (
        capability_id(CapabilityKind.HOOK, "lint", plugin_id="Quality")
        == "plugin:quality/hook/lint"
    )
    with pytest.raises(ValueError):
        normalize_id("../escape")


def test_snapshot_sorts_capabilities_and_copies_definitions() -> None:
    definitions = {"skill:z": {"description": "z"}}
    project = CapabilityRecord(
        capability_id="skill:z",
        public_name="z",
        kind=CapabilityKind.SKILL,
        source=ExtensionSource(ExtensionScope.PROJECT, Path("/workspace/z")),
    )
    user = CapabilityRecord(
        capability_id="skill:a",
        public_name="a",
        kind=CapabilityKind.SKILL,
        source=ExtensionSource(ExtensionScope.USER, Path("/home/user/a")),
    )

    snapshot = ExtensionSnapshot(1, "abc", (project, user), definitions)
    definitions["later"] = {}

    assert [item.public_name for item in snapshot.capabilities] == ["a", "z"]
    assert "later" not in snapshot.definitions
    with pytest.raises(TypeError):
        snapshot.definitions["new"] = {}  # type: ignore[index]


def test_snapshot_rejects_negative_generation() -> None:
    with pytest.raises(ValueError):
        ExtensionSnapshot(-1, "abc")
