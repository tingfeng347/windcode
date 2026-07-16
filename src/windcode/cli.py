from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from windcode.config import AppConfig, ConfigError, PermissionMode, ensure_user_config, load_config


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
        help=("compatibility switch for workspace_write/danger_full_access sandbox presets"),
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


def build_extensions_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="windcode extensions")
    parser.add_argument(
        "action", choices=("list", "inspect", "install", "enable", "disable", "reload", "trust")
    )
    parser.add_argument("target", nargs="?")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path)
    parser.add_argument("--enable", action="store_true", help="enable a plugin while installing")
    parser.add_argument("--untrust", action="store_true", help="remove workspace trust")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def build_sandbox_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="windcode sandbox")
    parser.add_argument("action", choices=("setup", "status"))
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _run_sandbox(argv: Sequence[str]) -> int:
    namespace = build_sandbox_parser().parse_args(argv)
    from windcode.sandbox import WindowsSandbox, setup_windows_sandbox

    workspace: Path = namespace.workspace.expanduser().resolve()
    if str(namespace.action) == "setup":
        payload: object = setup_windows_sandbox()
    else:
        status = WindowsSandbox(workspace).status
        payload = {
            "backend": status.backend,
            "state": status.state.value,
            "capabilities": {
                "filesystem_isolation": status.capabilities.filesystem_isolation,
                "network_isolation": status.capabilities.network_isolation,
                "process_isolation": status.capabilities.process_isolation,
            },
            "warning": status.warning,
            "remediation": status.remediation,
        }
    if namespace.json_output:
        print(json.dumps(payload, default=str, sort_keys=True))
    else:
        print(payload)
    return 0


async def _run_extensions(argv: Sequence[str]) -> int:
    namespace = build_extensions_parser().parse_args(argv)
    workspace: Path = namespace.workspace.expanduser().resolve()
    if not workspace.is_dir():  # noqa: ASYNC240 - CLI setup happens before concurrent work
        raise ConfigError(workspace, "workspace is not a directory")
    config = load_config(workspace, explicit_file=namespace.config)
    from windcode.sdk import Windcode

    async with Windcode.open(config, workspace=workspace) as client:
        action = str(namespace.action)
        target = None if namespace.target is None else str(namespace.target)
        if action == "list":
            value: object = await client.list_extensions()
        elif action == "inspect":
            if target is None:
                raise ConfigError("extensions inspect", "TARGET is required")
            value = await client.inspect_extension(target)
        elif action == "install":
            if target is None:
                raise ConfigError("extensions install", "PATH is required")
            value = await client.install_extension(Path(target), enable=bool(namespace.enable))
        elif action in {"enable", "disable"}:
            if target is None:
                raise ConfigError(f"extensions {action}", "TARGET is required")
            value = await client.set_extension_enabled(target, action == "enable")
        elif action == "trust":
            trust_target = (
                workspace if target is None else Path(target).expanduser().resolve()  # noqa: ASYNC240
            )
            value = await client.trust_extension_workspace(
                trust_target, not bool(namespace.untrust)
            )
        else:
            value = await client.reload_extensions()
        if namespace.json_output:
            from dataclasses import asdict, is_dataclass

            payload = (
                [asdict(item) for item in value]
                if isinstance(value, tuple)
                else asdict(value)
                if is_dataclass(value)
                else value
            )
            print(json.dumps(payload, default=str, sort_keys=True))
        else:
            if isinstance(value, tuple):
                for item in value:
                    print(getattr(item, "capability_id", str(item)))
            else:
                print(value)
    return 0


def run(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "extensions":
        try:
            return asyncio.run(_run_extensions(arguments[1:]))
        except ConfigError as exc:
            print(f"windcode: {exc}", file=sys.stderr)
            return 2
        except KeyError as exc:
            print(f"windcode: {exc}", file=sys.stderr)
            return 3
        except ValueError as exc:
            print(f"windcode: {exc}", file=sys.stderr)
            return 4
        except OSError as exc:
            print(f"windcode: {exc}", file=sys.stderr)
            return 5
    if arguments and arguments[0] == "sandbox":
        try:
            return _run_sandbox(arguments[1:])
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"windcode: {exc}", file=sys.stderr)
            return 5
    try:
        options = parse_options(arguments)
        if not options.workspace.is_dir():
            raise ConfigError(options.workspace, "workspace is not a directory")
        write_config_file = options.config_file or ensure_user_config()
        config = resolve_config(options)
    except (ConfigError, OSError) as exc:
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
        config_file=write_config_file,
    )
    app.run()
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    raise SystemExit(run(argv))
