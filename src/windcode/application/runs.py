from __future__ import annotations

import asyncio
from pathlib import Path

from windcode.application.configuration import ConfigurationApplication
from windcode.application.extensions import ExtensionApplication
from windcode.application.providers import ProviderApplication
from windcode.domain.events import RunRequest
from windcode.domain.tools import Tool
from windcode.runtime.parent_run import RunExtensionState
from windcode.runtime.run_builder import RunBuilder
from windcode.runtime.run_handle import RunHandle
from windcode.tools import ToolRegistry, create_builtin_registry


class RunApplication:
    """Own builtin tools, parent-run assembly, and active run handles."""

    def __init__(
        self,
        configuration: ConfigurationApplication,
        providers: ProviderApplication,
        extensions: ExtensionApplication,
        *,
        workspace: Path,
        state_root: Path,
    ) -> None:
        self.configuration = configuration
        self.providers = providers
        self.extensions = extensions
        self.workspace = workspace
        self.state_root = state_root
        self.registry: ToolRegistry | None = None
        self._handles: set[RunHandle] = set()
        self._opened = False
        self._closing = False

    def open(self) -> None:
        self.registry = create_builtin_registry(
            shell_timeout=self.configuration.current.budgets.shell_timeout_seconds,
        )
        self._opened = True
        self._closing = False

    def register_tool(self, tool: Tool, *, replace_existing: bool = False) -> None:
        registry = self.registry
        if registry is None:
            raise RuntimeError("register tools inside the Windcode async context")
        registry.register(tool, replace=replace_existing)

    def start(self, request: RunRequest) -> RunHandle:
        if not self._accepting_runs():
            raise RuntimeError("start runs inside the Windcode async context")
        handle = self.extensions.bind_run(
            lambda extension_state: self._builder(extension_state).start(request)
        )
        self._handles.add(handle)
        handle.add_done_callback(self._handles.discard)
        return handle

    def _accepting_runs(self) -> bool:
        return self._opened and not self._closing and self.registry is not None

    def _builder(self, extensions: RunExtensionState) -> RunBuilder:
        registry = self.registry
        if registry is None:
            raise RuntimeError("run builder requires an initialized tool registry")
        return RunBuilder(
            self.configuration.current,
            state_root=self.state_root,
            user_storage_root=self.configuration.user_storage_root(self.workspace),
            base_tools=registry,
            model_chain=self.providers.resolve,
            extensions=extensions,
        )

    def has_active_runs(self) -> bool:
        return any(not handle.done for handle in self._handles)

    def begin_close(self) -> None:
        self._closing = True

    def abort_close(self) -> None:
        self._closing = False

    def finish_close(self) -> None:
        self._opened = False
        self._closing = False

    async def cancel_all(self) -> None:
        handles = tuple(self._handles)
        await asyncio.gather(*(handle.cancel() for handle in handles))
