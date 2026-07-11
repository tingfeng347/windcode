from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from windcode.config import AppConfig, ConfigError, PermissionMode, load_config


@dataclass(frozen=True, slots=True)
class CLIOptions:
    workspace: Path
    config_file: Path | None
    model: str | None
    resume_session: str | None
    permission_mode: PermissionMode | None
    sandbox_enabled: bool | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="windcode", description="Terminal coding agent")
    parser.add_argument("workspace", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, help="explicit TOML configuration file")
    parser.add_argument("--model", help="provider alias or model override")
    parser.add_argument("--resume", metavar="SESSION_ID", help="resume an existing session")
    parser.add_argument(
        "--permission-mode",
        choices=tuple(mode.value for mode in PermissionMode),
        help="initial permission mode",
    )
    parser.add_argument(
        "--sandbox",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="enable or explicitly disable the Linux tool sandbox",
    )
    return parser


def parse_options(argv: Sequence[str] | None = None) -> CLIOptions:
    namespace = build_parser().parse_args(argv)
    raw_mode = namespace.permission_mode
    return CLIOptions(
        workspace=namespace.workspace.expanduser().resolve(),
        config_file=namespace.config,
        model=namespace.model,
        resume_session=namespace.resume,
        permission_mode=PermissionMode(raw_mode) if raw_mode is not None else None,
        sandbox_enabled=namespace.sandbox,
    )


def resolve_config(options: CLIOptions) -> AppConfig:
    overrides: dict[str, object] = {}
    if options.permission_mode is not None:
        overrides["permission"] = {"mode": options.permission_mode.value}
    if options.sandbox_enabled is not None:
        overrides["sandbox"] = {"enabled": options.sandbox_enabled}
    return load_config(
        options.workspace,
        explicit_file=options.config_file,
        overrides=overrides,
    )


def run(argv: Sequence[str] | None = None) -> int:
    try:
        options = parse_options(argv)
        if not options.workspace.is_dir():
            raise ConfigError(options.workspace, "workspace is not a directory")
        config = resolve_config(options)
    except ConfigError as exc:
        print(f"windcode: {exc}", file=sys.stderr)
        return 2

    from windcode.tui import WindcodeApp

    app = WindcodeApp(
        config,
        workspace=options.workspace,
        model=options.model,
        session_id=options.resume_session,
        permission_mode=(
            options.permission_mode.value if options.permission_mode is not None else None
        ),
        config_file=options.config_file,
    )
    app.run()
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    raise SystemExit(run(argv))
