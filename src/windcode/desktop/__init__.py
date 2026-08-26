"""Windcode desktop application entry point.

:func:`launch_desktop` starts the existing Web service on a loopback port and
opens a native system window via ``pywebview`` to host the frontend. The desktop
shell only owns window lifecycle; all business logic reuses the ASGI app and
static assets from :mod:`windcode.web`.
"""

from __future__ import annotations

from windcode.desktop.launcher import launch_desktop

__all__ = ["launch_desktop"]
