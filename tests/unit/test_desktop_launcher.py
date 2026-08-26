# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import json
import socket
import sys
import time
import urllib.request
from pathlib import Path

from windcode.desktop.launcher import (
    _system_python,
    pick_free_port,
    start_server,
)


def test_pick_free_port_returns_available_loopback_port() -> None:
    port = pick_free_port()
    assert 1 <= port <= 65535
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def test_start_server_serves_web_api_and_shuts_down(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    server, port = start_server(workspace, state_root=tmp_path / "state")
    try:
        url = f"http://127.0.0.1:{port}/api/v1/workspaces"
        body: dict[str, object] | None = None
        for _ in range(40):
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    body = json.loads(response.read().decode("utf-8"))
                    break
            except OSError:
                time.sleep(0.05)
        assert body is not None
        items = body["items"]
        assert isinstance(items, list)
        assert any(str(workspace) == item["path"] for item in items)  # type: ignore[index]
    finally:
        server.should_exit = True


def test_system_python_finds_external_interpreter_on_linux() -> None:
    if not sys.platform.startswith("linux"):
        return
    python = _system_python()
    assert python is not None
    assert Path(python).resolve() != Path(sys.executable).resolve()
