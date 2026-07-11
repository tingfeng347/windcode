from pathlib import Path

from windcode.config import (
    AppConfig,
    PermissionConfig,
    PermissionMode,
    ProviderConfig,
    ProviderProtocol,
    load_config,
    save_model_config,
)


def provider(model: str = "model") -> ProviderConfig:
    return ProviderConfig(
        protocol=ProviderProtocol.OPENAI_RESPONSES,
        model=model,
        api_key_env="MODEL_API_KEY",
    )


def test_saves_models_without_overwriting_other_project_settings(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    user = tmp_path / "user.toml"
    user.touch()
    path.write_text('[permission]\nmode = "plan"\n', encoding="utf-8")
    previous = AppConfig(permission=PermissionConfig(mode=PermissionMode.PLAN))
    updated = AppConfig(
        providers={"codex": provider("gpt")},
        primary_provider="codex",
        permission=PermissionConfig(mode=PermissionMode.PLAN),
    )

    save_model_config(path, previous, updated)

    content = path.read_text(encoding="utf-8")
    assert "MODEL_API_KEY" in content
    assert "secret" not in content
    loaded = load_config(tmp_path, user_file=user, project_file=path)
    assert loaded.permission.mode is PermissionMode.PLAN
    assert loaded.primary_provider == "codex"
    assert loaded.providers["codex"].model == "gpt"


def test_deleted_inherited_model_stays_disabled_after_reload(tmp_path: Path) -> None:
    user = tmp_path / "user.toml"
    user.write_text(
        'primary_provider = "inherited"\n'
        "[providers.inherited]\n"
        'protocol = "openai_responses"\n'
        'model = "model"\n'
        'api_key_env = "MODEL_API_KEY"\n',
        encoding="utf-8",
    )
    project = tmp_path / "project.toml"
    project.touch()
    previous = load_config(tmp_path, user_file=user, project_file=project)

    save_model_config(project, previous, AppConfig())

    loaded = load_config(tmp_path, user_file=user, project_file=project)
    assert loaded.providers == {}
    assert loaded.primary_provider is None


def test_higher_layer_can_reenable_inherited_model_alias(tmp_path: Path) -> None:
    user = tmp_path / "user.toml"
    user.write_text(
        'disabled_providers = ["shared"]\n'
        "[providers.shared]\n"
        'protocol = "openai_responses"\n'
        'model = "old"\n'
        'api_key_env = "MODEL_API_KEY"\n',
        encoding="utf-8",
    )
    project = tmp_path / "project.toml"
    project.touch()
    previous = load_config(tmp_path, user_file=user, project_file=project)
    updated = AppConfig(providers={"shared": provider("new")}, primary_provider="shared")

    save_model_config(project, previous, updated)

    loaded = load_config(tmp_path, user_file=user, project_file=project)
    assert loaded.providers["shared"].model == "new"
    assert loaded.primary_provider == "shared"
