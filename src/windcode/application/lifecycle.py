from __future__ import annotations

import asyncio
from pathlib import Path

from windcode.application.configuration import ConfigurationApplication
from windcode.application.extensions import ExtensionApplication
from windcode.application.providers import ProviderApplication
from windcode.application.runs import RunApplication
from windcode.memory import MemoryService


class ApplicationLifecycle:
    """Own ordered startup, shutdown, and cross-module lifecycle state."""

    def __init__(
        self,
        configuration: ConfigurationApplication,
        providers: ProviderApplication,
        extensions: ExtensionApplication,
        runs: RunApplication,
    ) -> None:
        self.configuration = configuration
        self.providers = providers
        self.extensions = extensions
        self.runs = runs
        self.memory_service: MemoryService | None = None
        self._lock = asyncio.Lock()
        self._opened = False
        self._closing = False

    async def open(self, *, state_root: Path, workspace: Path) -> None:
        if self._closing:
            raise RuntimeError("Windcode client is already open")
        async with self._lock:
            if self._opened or self._closing:
                raise RuntimeError("Windcode client is already open")
            self._opened = True
            state_root.mkdir(  # noqa: ASYNC240 - local state is initialized before concurrent work
                parents=True, exist_ok=True
            )
            if self.configuration.current.memory.enabled:
                self.memory_service = MemoryService(state_root, workspace)
            await self.providers.open()
            self.runs.open()
            await self.extensions.open()

    async def close(self) -> None:
        async with self._lock:
            if not self._opened:
                return
            self._closing = True
            self.runs.begin_close()
            try:
                await self.runs.cancel_all()
                await asyncio.gather(
                    self.extensions.aclose(),
                    self.providers.aclose(),
                    return_exceptions=True,
                )
            except BaseException:
                self.runs.abort_close()
                self._closing = False
                raise
            else:
                self.runs.finish_close()
                self._opened = False
                self._closing = False
