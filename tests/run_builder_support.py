from collections.abc import Callable
from pathlib import Path

from windcode.auth import FileCredentialStore
from windcode.config import AppConfig
from windcode.extensions import ExtensionSnapshot
from windcode.providers import ModelTarget
from windcode.runtime.parent_run import RunExtensionState
from windcode.runtime.run_builder import RunBuilder
from windcode.runtime.subagents import ChildRunPreparer
from windcode.tools import ToolRegistry


def child_preparer(
    config: AppConfig,
    *,
    state_root: Path,
    parent_tools: ToolRegistry,
    model_chain: Callable[[str | None], tuple[ModelTarget, ...]],
    extension_snapshot: ExtensionSnapshot | None = None,
    default_model: str | None = None,
) -> ChildRunPreparer:
    snapshot = extension_snapshot or ExtensionSnapshot(0, "test")
    builder = RunBuilder(
        config,
        state_root=state_root,
        user_storage_root=state_root / "user",
        base_tools=parent_tools,
        model_chain=model_chain,
        extensions=RunExtensionState(
            snapshot,
            FileCredentialStore(state_root / "auth.json"),
            None,
            None,
            {},
            set(),
            24,
        ),
    )
    return builder.bind_child(parent_tools, default_model=default_model)
