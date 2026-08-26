# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

import uvicorn

from windcode.web.app import create_web_app

__all__ = ["launch_desktop", "pick_free_port", "start_server"]


def pick_free_port() -> int:
    """Return a currently available loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_server(
    workspace: Path,
    *,
    state_root: Path | None = None,
    port: int | None = None,
) -> tuple[uvicorn.Server, int]:
    """Start the Web service on a daemon thread and wait until it is listening.

    The caller is responsible for setting ``server.should_exit`` to ``True``
    once the desktop window has closed.
    """
    selected_port = port if port is not None else pick_free_port()
    app = create_web_app(initial_workspace=workspace, state_root=state_root)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=selected_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="windcode-desktop-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 15.0
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            server.should_exit = True
            raise RuntimeError("Windcode Web service failed to start")
        time.sleep(0.05)
    return server, selected_port


# --- Linux: system Python + WebKitGTK (zero extra dependencies) -------------

_WEBKIT_SCRIPT = textwrap.dedent(
    """\
    import sys
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('WebKit2', '4.1')
    from gi.repository import Gtk, WebKit2, GLib

    url, title, width, height = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])

    win = Gtk.Window()
    win.set_title(title)
    win.set_default_size(width, height)
    win.connect('destroy', Gtk.main_quit)

    web = WebKit2.WebView()
    settings = web.get_settings()
    settings.set_enable_developer_extras(False)
    web.load_uri(url)
    win.add(web)
    win.show_all()
    Gtk.main()
    """
)


def _system_python() -> str | None:
    """Return the path to the system Python (outside any venv).

    ``sys.executable`` inside a ``uv`` venv points to the isolated interpreter
    which cannot see system packages like ``gi``.  We look for ``python3`` on
    ``PATH`` and verify it is different from the current interpreter.
    """
    if not sys.platform.startswith("linux"):
        return None
    candidates = [
        "/usr/bin/python3",
        "/usr/local/bin/python3",
    ]
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            if Path(candidate).resolve() != Path(sys.executable).resolve():
                return candidate
    return None


def _launch_webkitgtk(url: str, *, title: str, width: int, height: int) -> int:
    """Spawn a system Python process running a WebKitGTK window.

    On Linux the system Python typically has ``gi`` + ``WebKit2`` bindings
    available (via ``python-gobject`` and ``webkit2gtk``), while ``uv`` venvs
    isolate those system packages.  Running the window in a separate system
    Python process avoids bundling a full Chromium (Qt WebEngine ~170 MB) and
    uses the native system WebView instead.
    """
    python = _system_python()
    if python is None:
        raise RuntimeError("no system Python found for WebKitGTK backend")
    script = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8")
    try:
        script.write(_WEBKIT_SCRIPT)
        script.flush()
        script.close()
        proc = subprocess.run(
            [python, script.name, url, title, str(width), str(height)],
            check=False,
        )
        return proc.returncode
    finally:
        Path(script.name).unlink(missing_ok=True)


# --- Other platforms: pywebview fallback -------------------------------------


def _launch_pywebview(url: str, *, title: str, width: int, height: int) -> int:
    """Open a window via ``pywebview`` (optional dependency ``windcode[desktop]``).

    Used on Windows / macOS where ``pywebview`` reuses the native EdgeChromium /
    WebKit runtime.  On Linux it is only reached when the WebKitGTK path is
    unavailable.
    """
    import webview

    webview.create_window(title, url, width=width, height=height, min_size=(720, 480))
    webview.start()
    return 0


def _try_webkitgtk() -> bool:
    """Return ``True`` if the system Python can import ``gi`` + ``WebKit2``."""
    python = _system_python()
    if python is None:
        return False
    probe = textwrap.dedent(
        """\
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('WebKit2', '4.1')
        from gi.repository import WebKit2
        """
    )
    try:
        subprocess.run(
            [python, "-c", probe],
            check=True,
            capture_output=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def launch_desktop(
    workspace: Path,
    *,
    state_root: Path | None = None,
    width: int = 1280,
    height: int = 820,
    title: str = "Windcode",
) -> int:
    """Open a native desktop window hosting the Windcode Web workspace.

    On Linux the window is rendered by the system WebKitGTK via a separate
    system Python process (no bundled Chromium).  On Windows / macOS, or when
    WebKitGTK is unavailable, it falls back to ``pywebview``
    (``windcode[desktop]``).  The local server is shut down when the window
    closes.
    """
    server, port = start_server(workspace, state_root=state_root)
    url = f"http://127.0.0.1:{port}"
    try:
        if _try_webkitgtk():
            _launch_webkitgtk(url, title=title, width=width, height=height)
        else:
            _launch_pywebview(url, title=title, width=width, height=height)
    finally:
        server.should_exit = True
    return 0
