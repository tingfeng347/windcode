from __future__ import annotations

from pathlib import Path

from windcode.config import (
    AppConfig,
    ExtensionConfig,
    save_extension_config,
    save_memory_config,
    save_model_config,
)


class ConfigurationApplication:
    """Own the live configuration and publish persisted changes atomically."""

    def __init__(self, config: AppConfig) -> None:
        self.current = config

    def user_storage_root(self, workspace: Path) -> Path:
        root = Path(self.current.storage.user_storage_root).expanduser()
        if not root.is_absolute():
            root = workspace / root
        return root.resolve()

    def replace_models(self, config: AppConfig, *, config_file: Path) -> None:
        save_model_config(config_file, self.current, config)
        self.current = config

    def set_memory_enabled(self, enabled: bool, *, config_file: Path) -> AppConfig:
        updated_memory = self.current.memory.model_copy(update={"enabled": enabled})
        updated = self.current.model_copy(update={"memory": updated_memory})
        save_memory_config(config_file, updated)
        self.current = updated
        return updated

    def replace_extensions(self, extensions: ExtensionConfig, *, config_file: Path) -> AppConfig:
        updated = self.current.model_copy(update={"extensions": extensions})
        save_extension_config(config_file, extensions)
        self.current = updated
        return updated
