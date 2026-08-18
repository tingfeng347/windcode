from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

import uvicorn

from windcode.web.app import create_web_app


def run_web_server(
    workspace: Path,
    *,
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        timer = threading.Timer(0.8, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    uvicorn.run(create_web_app(initial_workspace=workspace), host="127.0.0.1", port=port)
