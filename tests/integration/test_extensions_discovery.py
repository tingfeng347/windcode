import sys
from pathlib import Path

import pytest

from windcode import Windcode
from windcode.config import load_config


@pytest.mark.asyncio
async def test_project_mcp_connects_without_workspace_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    config_dir = workspace / ".windcode"
    config_dir.mkdir(parents=True)
    server = Path(__file__).parents[1] / "contract" / "mcp" / "server.py"
    (config_dir / "config.toml").write_text(
        "[extensions]\nenabled=true\n"
        "[extensions.mcp_servers.project]\n"
        f"transport='stdio'\ncommand={sys.executable!r}\n"
        f"args=[{str(server)!r}]\nrequired=true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    config = load_config(workspace)

    async with Windcode.open(config, state_root=tmp_path / "state", workspace=workspace) as client:
        await client.wait_for_required_mcp()
        record = (await client.inspect_extension("mcp_server:project"))[0]

        assert record.trusted
        assert record.enabled
