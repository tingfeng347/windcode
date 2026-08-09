from __future__ import annotations

from pathlib import Path

from windcode.config import AppConfig, save_memory_config, save_model_config


class ConfigurationApplication:
    """Own the live configuration and publish persisted changes atomically."""

    def __init__(self, config: AppConfig) -> None:
        self.current = config

    def replace_models(self, config: AppConfig, *, config_file: Path) -> None:
        save_model_config(config_file, self.current, config)
        self.current = config

    def set_memory_enabled(self, enabled: bool, *, config_file: Path) -> AppConfig:
        updated_memory = self.current.memory.model_copy(update={"enabled": enabled})
        updated = self.current.model_copy(update={"memory": updated_memory})
        save_memory_config(config_file, updated)
        self.current = updated
        return updated
