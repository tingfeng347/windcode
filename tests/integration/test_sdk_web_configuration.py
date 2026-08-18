from pathlib import Path

import pytest

from windcode import Windcode
from windcode.config import AppConfig, McpStdioConfig


@pytest.mark.asyncio
async def test_sdk_manages_skill_roots_and_mcp_servers_through_public_methods(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    config_file = tmp_path / "project" / ".windcode" / "config.toml"
    skill_root = tmp_path / "shared-skills"
    skill_root.mkdir()

    async with Windcode.open(AppConfig(), state_root=state_root, workspace=tmp_path) as client:
        await client.add_skill_root(skill_root, config_file=config_file)
        await client.upsert_mcp_server(
            "disabled",
            McpStdioConfig(command="never-started", enabled=False),
            config_file=config_file,
        )

        assert client.config.extensions.skill_roots == (str(skill_root.resolve()),)
        assert "disabled" in client.config.extensions.mcp_servers
        assert client.mcp_server_states()["disabled"] == "disabled"

        await client.remove_mcp_server("disabled", config_file=config_file)
        await client.remove_skill_root(skill_root, config_file=config_file)

        assert client.config.extensions.skill_roots == ()
        assert client.config.extensions.mcp_servers == {}

    content = config_file.read_text(encoding="utf-8")
    assert "never-started" not in content
