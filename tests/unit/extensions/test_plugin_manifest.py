from pathlib import Path

import pytest

from windcode.extensions.plugins.manifest import parse_plugin_manifest


def _plugin(tmp_path: Path, extra: str = "") -> Path:
    root = tmp_path / "plugin"
    (root / ".windcode-plugin").mkdir(parents=True)
    (root / "skills" / "review").mkdir(parents=True)
    (root / "hooks").mkdir()
    (root / "mcp").mkdir()
    (root / "skills" / "review" / "SKILL.md").write_text(
        "---\nname: review\ndescription: review\n---\nbody"
    )
    (root / "hooks" / "guard.toml").write_text("event='tool_before_policy'")
    (root / "mcp" / "analysis.toml").write_text("transport='stdio'")
    (root / ".windcode-plugin" / "plugin.toml").write_text(
        """manifest_version = 1
id = "com.example.review"
name = "Review Helper"
version = "1.0.0"
windcode = ">=0.3.0,<0.5.0"
skills = [{ id = "review", path = "skills/review" }]
hooks = [{ id = "guard", path = "hooks/guard.toml" }]
commands = [{ name = "review", target = "skill:review" }]
[mcp_servers.analysis]
path = "mcp/analysis.toml"
[permissions]
effects = ["read", "process"]
[data]
persistent = true
"""
        + extra
    )
    return root


def test_parse_complete_declarative_manifest(tmp_path: Path) -> None:
    manifest = parse_plugin_manifest(_plugin(tmp_path))

    assert manifest.plugin_id == "com.example.review"
    assert manifest.skills[0].component_id == "review"
    assert manifest.mcp_servers[0].component_id == "analysis"
    assert manifest.commands[0].target == "skill:review"
    assert manifest.persistent_data


def test_manifest_accepts_current_minor_compatibility_range(tmp_path: Path) -> None:
    root = _plugin(tmp_path)

    assert parse_plugin_manifest(root).windcode == ">=0.3.0,<0.5.0"


@pytest.mark.parametrize("compatibility", [">=0.1,<0.2", ">=1.0,<2.0", "not-a-range"])
def test_manifest_rejects_incompatible_version_range(tmp_path: Path, compatibility: str) -> None:
    root = _plugin(tmp_path)
    path = root / ".windcode-plugin" / "plugin.toml"
    path.write_text(path.read_text().replace(">=0.3.0,<0.5.0", compatibility))

    with pytest.raises(ValueError, match="incompatible Windcode version range"):
        parse_plugin_manifest(root)


def test_manifest_rejects_arbitrary_command_fields(tmp_path: Path) -> None:
    root = _plugin(tmp_path)
    path = root / ".windcode-plugin" / "plugin.toml"
    path.write_text(
        path.read_text().replace(
            'commands = [{ name = "review", target = "skill:review" }]',
            'commands = [{ name = "review", target = "skill:review", shell = "touch /tmp/x" }]',
        )
    )
    with pytest.raises(ValueError, match="require only"):
        parse_plugin_manifest(root)


def test_manifest_rejects_path_escape_without_reading_it(tmp_path: Path) -> None:
    root = _plugin(tmp_path)
    path = root / ".windcode-plugin" / "plugin.toml"
    path.write_text(path.read_text().replace("skills/review", "../outside"))

    with pytest.raises(ValueError, match="escapes"):
        parse_plugin_manifest(root)


def test_manifest_rejects_unknown_and_incompatible_fields(tmp_path: Path) -> None:
    root = _plugin(tmp_path, "\nentrypoint = 'plugin.py'\n")
    with pytest.raises(ValueError, match="unknown"):
        parse_plugin_manifest(root)


def test_manifest_rejects_unknown_effect_and_non_hostname_network_entry(tmp_path: Path) -> None:
    root = _plugin(tmp_path)
    path = root / ".windcode-plugin" / "plugin.toml"
    path.write_text(path.read_text().replace('"read", "process"', '"read", "root"'))
    with pytest.raises(ValueError, match="unknown plugin permission effect"):
        parse_plugin_manifest(root)

    path.write_text(
        path.read_text()
        .replace('"read", "root"', '"read", "process"')
        .replace("[data]", 'network_hosts = ["https://api.example.com/path"]\n[data]')
    )
    with pytest.raises(ValueError, match="must be hostnames"):
        parse_plugin_manifest(root)
