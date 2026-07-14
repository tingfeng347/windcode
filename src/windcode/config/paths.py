from pathlib import Path


def default_user_storage_root() -> Path:
    """Return the single user-level root used by Windcode."""
    return Path.home() / ".windcode"


def default_user_config_path() -> Path:
    return default_user_storage_root() / "config.toml"
