from pathlib import Path

import pytest
from pydantic import ValidationError

from windcode.config.loader import load_config
from windcode.config.models import AppConfig, ExtensionConfig


def test_extensions_are_enabled_with_empty_mcp_servers_by_default(tmp_path: Path) -> None:
    user = tmp_path / "user.toml"
    user.write_text("", encoding="utf-8")
    config = load_config(tmp_path, user_file=user)

    assert config.extensions == ExtensionConfig()
    assert config.extensions.enabled
    assert config.extensions.skill_roots == ()
    assert config.extensions.mcp_servers == {}


def test_project_mcp_provenance_survives_layer_merge(tmp_path: Path) -> None:
    user = tmp_path / "user.toml"
    project = tmp_path / "project.toml"
    user.write_text(
        "[extensions]\nenabled=true\n"
        '[extensions.mcp_servers.user]\ntransport="stdio"\ncommand="user-server"\n',
        encoding="utf-8",
    )
    project.write_text(
        '[extensions.mcp_servers.project]\ntransport="stdio"\ncommand="project-server"\n',
        encoding="utf-8",
    )

    config = load_config(tmp_path, user_file=user, project_file=project)

    assert set(config.extensions.mcp_servers) == {"project", "user"}
    assert config.extensions.project_mcp_servers == frozenset({"project"})


def test_extension_config_accepts_secret_references() -> None:
    config = AppConfig.model_validate(
        {
            "extensions": {
                "enabled": True,
                "mcp_servers": {
                    "local": {
                        "command": "example-mcp",
                        "env": {"TOKEN": {"env": "EXAMPLE_TOKEN"}},
                    },
                    "remote": {
                        "transport": "streamable_http",
                        "enable": False,
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": {"credential": "example-token"}},
                    },
                },
            }
        }
    )

    assert config.extensions.enabled
    assert set(config.extensions.mcp_servers) == {"local", "remote"}
    assert config.extensions.mcp_servers["local"].enabled
    assert not config.extensions.mcp_servers["remote"].enabled


@pytest.mark.parametrize(
    "server",
    [
        {"command": "example-mcp", "env": {"TOKEN": "plaintext"}},
        {
            "transport": "streamable_http",
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer secret"},
        },
    ],
)
def test_extension_config_rejects_plaintext_secrets(server: object) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"extensions": {"mcp_servers": {"unsafe": server}}})


def test_extension_config_is_strict_and_bounded() -> None:
    with pytest.raises(ValidationError):
        ExtensionConfig.model_validate({"unknown": True})
    with pytest.raises(ValidationError):
        ExtensionConfig(max_scan_depth=100)
