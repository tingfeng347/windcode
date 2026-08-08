import json
from pathlib import Path

import pytest

from windcode.domain.tools import ToolContext
from windcode.extensions.models import (
    CapabilityKind,
    CapabilityRecord,
    ExtensionScope,
    ExtensionSnapshot,
    ExtensionSource,
)
from windcode.extensions.skills.loader import SkillLoader
from windcode.extensions.skills.parser import parse_skill_metadata
from windcode.extensions.skills.tools import SkillCatalog, SkillRuntime, register_skill_tools
from windcode.tools.registry import ToolRegistry


def test_search_is_compact_and_load_returns_sourced_context(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: review\ndescription: Review code\n---\nsecret body")
    metadata = parse_skill_metadata(root)
    record = CapabilityRecord(
        "skill:review",
        "review",
        CapabilityKind.SKILL,
        ExtensionSource(ExtensionScope.USER, root),
    )
    catalog = SkillCatalog(
        ExtensionSnapshot(1, "x", (record,), {record.capability_id: metadata}),
        SkillLoader(max_content_bytes=1024),
    )

    search = catalog.search("code")
    content, context = catalog.load("$review")

    assert search[0].description == "Review code"
    assert "secret body" not in repr(search)
    assert content.content.endswith("secret body")
    assert context.source_id == record.source.source_id


def test_untrusted_skill_cannot_load(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: review\ndescription: Review\n---\nbody")
    metadata = parse_skill_metadata(root)
    record = CapabilityRecord(
        "skill:review",
        "review",
        CapabilityKind.SKILL,
        ExtensionSource(ExtensionScope.PROJECT, root),
        trusted=False,
    )
    catalog = SkillCatalog(
        ExtensionSnapshot(1, "x", (record,), {record.capability_id: metadata}),
        SkillLoader(max_content_bytes=1024),
    )
    with pytest.raises(ValueError, match="missing or ambiguous"):
        catalog.load("review")


@pytest.mark.asyncio
async def test_skill_tools_search_without_body_and_load_context_once(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\nsecret instructions"
    )
    metadata = parse_skill_metadata(root)
    record = CapabilityRecord(
        "skill:review",
        "review",
        CapabilityKind.SKILL,
        ExtensionSource(ExtensionScope.USER, root),
    )
    runtime = SkillRuntime(
        SkillCatalog(
            ExtensionSnapshot(1, "x", (record,), {record.capability_id: metadata}),
            SkillLoader(max_content_bytes=1024),
        )
    )

    async def activate(selector: str):
        return runtime.activate(selector)

    registry = ToolRegistry()
    register_skill_tools(registry, runtime, activate)
    context = ToolContext(tmp_path, "run", lambda: False)

    search = await registry.execute("search_skills", context, {"query": "code"})
    assert "Review code" in search.output
    assert "secret instructions" not in search.output

    first = await registry.execute("load_skill", context, {"name": "$review"})
    assert json.loads(first.output)["status"] == "loaded"
    sourced = runtime.drain_context()
    assert sourced[0].source_id == record.source.source_id
    assert "secret instructions" in sourced[0].content

    second = await registry.execute("load_skill", context, {"name": "review"})
    assert json.loads(second.output)["status"] == "already_loaded"
    assert runtime.drain_context() == ()


@pytest.mark.asyncio
async def test_load_skill_returns_structured_error_for_unavailable_skill(tmp_path: Path) -> None:
    runtime = SkillRuntime(
        SkillCatalog(ExtensionSnapshot(1, "x"), SkillLoader(max_content_bytes=1024))
    )

    async def activate(selector: str):
        return runtime.activate(selector)

    registry = ToolRegistry()
    register_skill_tools(registry, runtime, activate)
    result = await registry.execute(
        "load_skill", ToolContext(tmp_path, "run", lambda: False), {"name": "missing"}
    )

    assert result.is_error
    assert json.loads(result.output)["error"] == "skill_unavailable"
