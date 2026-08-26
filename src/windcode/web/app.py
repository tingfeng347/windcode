# pyright: reportUnusedFunction=false

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from windcode.config import (
    McpServerConfig,
    PermissionMode,
    ProviderProtocol,
    default_user_storage_root,
)
from windcode.domain.events import ApprovalResponse, UserResponse
from windcode.extensions.models import ExtensionScope
from windcode.providers import ProviderDraft
from windcode.providers.registry import ProviderConfigurationError
from windcode.tui.commands import COMMAND_CATALOG, COMMANDS
from windcode.version import VERSION
from windcode.web.runtime import WebRuntimeManager, WorkspaceStore, json_value


class LoopbackGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        host = request.url.hostname
        if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return Response("Forbidden", status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and urlparse(origin).hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            return Response("Forbidden", status_code=403)
        return await call_next(request)


class WorkspaceRequest(BaseModel):
    path: str


class RunStartRequest(BaseModel):
    prompt: str = Field(min_length=1)
    session_id: str | None = None
    model: str | None = None
    permission_mode: PermissionMode | None = None


class PermissionRequest(BaseModel):
    mode: PermissionMode


class ApprovalRequest(BaseModel):
    request_id: str
    decision: str


class UserAnswerRequest(BaseModel):
    request_id: str
    answers: dict[str, str]


class ProviderRequest(BaseModel):
    alias: str
    protocol: ProviderProtocol
    model: str
    provider_id: str | None = None
    api_key_env: str | None = None
    credential_id: str | None = None
    base_url: str | None = None
    secret: str | None = None
    editing_alias: str | None = None

    def draft(self, *, default_credential_id: str | None = None) -> ProviderDraft:
        data = self.model_dump()
        if default_credential_id is not None and data["credential_id"] in {None, self.alias}:
            data["credential_id"] = default_credential_id
        return ProviderDraft(**data)


class FallbackRequest(BaseModel):
    aliases: tuple[str, ...]


class SkillRootRequest(BaseModel):
    path: str


class CapabilityStateRequest(BaseModel):
    enabled: bool


class CapabilityTrustRequest(BaseModel):
    trusted: bool
    scope: ExtensionScope | None = None


class PluginInstallRequest(BaseModel):
    path: str
    enable: bool = False


class McpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: Literal["stdio", "streamable_http"]
    enable: bool = True
    required: bool = False
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: dict[str, dict[str, str]] = Field(default_factory=dict[str, dict[str, str]])
    url: str | None = None
    headers: dict[str, dict[str, str]] = Field(default_factory=dict[str, dict[str, str]])

    def config(self) -> McpServerConfig:
        raw = self.model_dump(exclude_none=True)
        return cast(McpServerConfig, TypeAdapter(McpServerConfig).validate_python(raw))


def _runtime(request: Request) -> WebRuntimeManager:
    return cast(WebRuntimeManager, request.app.state.runtime)


def _records(client: Any, session_id: str) -> list[dict[str, object]]:
    return [record.to_dict() for record in client.load_session_records(session_id)]


def create_web_app(
    *,
    initial_workspace: Path | None = None,
    web_root: Path | None = None,
    state_root: Path | None = None,
) -> FastAPI:
    selected_web_root = web_root or default_user_storage_root() / "web"
    store = WorkspaceStore(selected_web_root / "workspaces.json")
    if initial_workspace is not None:
        store.add(initial_workspace)
    manager = WebRuntimeManager(store, state_root=state_root)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        app.state.runtime = manager
        yield
        await manager.close()

    app = FastAPI(title="Windcode Web", version=VERSION, lifespan=lifespan)
    app.add_middleware(LoopbackGuardMiddleware)

    @app.exception_handler(KeyError)
    async def key_error_handler(_request: Request, exc: KeyError) -> Response:
        return Response(str(exc), status_code=404)

    @app.exception_handler(ProviderConfigurationError)
    async def provider_error_handler(
        _request: Request, exc: ProviderConfigurationError
    ) -> Response:
        return Response(str(exc), status_code=409)

    @app.get("/api/v1/bootstrap")
    async def bootstrap(request: Request, workspace_id: str | None = None) -> dict[str, object]:
        runtime = await _runtime(request).get(workspace_id)
        client = runtime.require_client()
        return {
            "version": VERSION,
            "workspace": runtime.entry.to_dict(),
            "workspaces": [item.to_dict() for item in _runtime(request).store.list()],
            "permission_mode": client.config.permission.mode.value,
            "providers": json_value(runtime.require_providers().snapshot()),
            "primary_provider": client.config.primary_provider,
            "fallback_chain": list(client.config.fallback_chain),
            "model_ready": client.can_resolve_model(),
            "mcp_status": asdict(client.mcp_startup_status),
            "skill_roots": list(client.config.extensions.skill_roots),
        }

    @app.get("/api/v1/workspaces/{workspace_id}/commands")
    async def commands(workspace_id: str, request: Request) -> dict[str, object]:
        runtime = await _runtime(request).get(workspace_id)
        routes = runtime.require_client().extension_commands(reserved=COMMANDS)
        return {
            "items": [
                {"name": item.name, "description": item.description, "target": None}
                for item in COMMAND_CATALOG
            ]
            + [
                {
                    "name": route.name,
                    "description": f"插件命令 · {route.source_id}",
                    "target": route.target,
                }
                for route in routes
            ]
        }

    @app.get("/api/v1/workspaces")
    async def list_workspaces(request: Request) -> dict[str, object]:
        store = _runtime(request).store
        return {
            "selected": store.selected,
            "items": [entry.to_dict() for entry in store.list()],
        }

    @app.post("/api/v1/workspaces")
    async def add_workspace(body: WorkspaceRequest, request: Request) -> dict[str, str]:
        try:
            return _runtime(request).store.add(Path(body.path)).to_dict()
        except (OSError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/v1/workspaces/{workspace_id}/select")
    async def select_workspace(workspace_id: str, request: Request) -> dict[str, str]:
        try:
            return _runtime(request).store.select(workspace_id).to_dict()
        except KeyError as exc:
            raise HTTPException(404, "workspace not found") from exc

    @app.delete("/api/v1/workspaces/{workspace_id}")
    async def remove_workspace(workspace_id: str, request: Request) -> dict[str, str]:
        try:
            return (await _runtime(request).remove(workspace_id)).to_dict()
        except KeyError as exc:
            raise HTTPException(404, "workspace not found") from exc

    @app.get("/api/v1/directories")
    async def browse_directories(path: str | None = None) -> dict[str, object]:
        try:
            selected = (
                Path(path).expanduser().resolve(strict=True)  # noqa: ASYNC240 - local directory browser
                if path
                else Path.home().resolve()
            )
        except OSError as exc:
            raise HTTPException(400, "path does not exist") from exc
        if not selected.is_dir():
            raise HTTPException(400, "path is not a directory")
        try:
            items = [
                {"name": item.name, "path": str(item)}
                for item in sorted(selected.iterdir(), key=lambda value: value.name.casefold())
                if item.is_dir() and not item.name.startswith(".")
            ]
        except OSError as exc:
            raise HTTPException(400, "directory cannot be read") from exc
        return {"path": str(selected), "parent": str(selected.parent), "items": items}

    @app.get("/api/v1/workspaces/{workspace_id}/directories")
    async def list_directories(workspace_id: str, request: Request, path: str | None = None):
        runtime = await _runtime(request).get(workspace_id)
        selected = (
            Path(path).expanduser().resolve()  # noqa: ASYNC240 - local directory browser
            if path
            else runtime.entry.path
        )
        if not selected.is_dir():
            raise HTTPException(400, "path is not a directory")
        return {
            "path": str(selected),
            "parent": str(selected.parent),
            "items": [
                {"name": item.name, "path": str(item)}
                for item in sorted(selected.iterdir(), key=lambda value: value.name.casefold())
                if item.is_dir() and not item.name.startswith(".")
            ],
        }

    @app.get("/api/v1/workspaces/{workspace_id}/sessions")
    async def sessions(workspace_id: str, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        return [item.to_dict() for item in runtime.require_client().list_sessions()]

    @app.get("/api/v1/workspaces/{workspace_id}/sessions/{session_id}")
    async def session(workspace_id: str, session_id: str, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        if not runtime.require_client().session_exists(session_id):
            raise HTTPException(404, "session not found")
        return {"session_id": session_id, "records": _records(runtime.require_client(), session_id)}

    @app.delete("/api/v1/workspaces/{workspace_id}/sessions")
    async def clear_sessions(workspace_id: str, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        if runtime.runs:
            raise HTTPException(409, "cannot clear sessions while a run is active")
        return {"deleted": runtime.require_client().clear_sessions()}

    @app.post("/api/v1/workspaces/{workspace_id}/sessions/{session_id}/rewind")
    async def rewind(workspace_id: str, session_id: str, record_id: str, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        return runtime.require_client().rewind_session(session_id, record_id).to_dict()

    @app.post("/api/v1/workspaces/{workspace_id}/runs")
    async def start_run(workspace_id: str, body: RunStartRequest, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        try:
            session_id, run_id = await runtime.start_run(
                body.prompt,
                session_id=body.session_id,
                model=body.model,
                permission_mode=None
                if body.permission_mode is None
                else body.permission_mode.value,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"session_id": session_id, "run_id": run_id}

    @app.post("/api/v1/workspaces/{workspace_id}/runs/{run_id}/cancel")
    async def cancel_run(workspace_id: str, run_id: str, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        await runtime.get_run(run_id).cancel()
        return {"ok": True}

    @app.post("/api/v1/workspaces/{workspace_id}/runs/{run_id}/compact")
    async def compact_run(workspace_id: str, run_id: str, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        await runtime.get_run(run_id).compact()
        return {"ok": True}

    @app.patch("/api/v1/workspaces/{workspace_id}/runs/{run_id}/permission")
    async def set_permission(
        workspace_id: str, run_id: str, body: PermissionRequest, request: Request
    ):
        runtime = await _runtime(request).get(workspace_id)
        mode = runtime.get_run(run_id).set_permission_mode(body.mode)
        return {"mode": mode.value}

    @app.post("/api/v1/workspaces/{workspace_id}/runs/{run_id}/approval")
    async def approve(workspace_id: str, run_id: str, body: ApprovalRequest, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        await runtime.get_run(run_id).respond(ApprovalResponse(body.request_id, body.decision))
        return {"ok": True}

    @app.post("/api/v1/workspaces/{workspace_id}/runs/{run_id}/answers")
    async def answer(workspace_id: str, run_id: str, body: UserAnswerRequest, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        await runtime.get_run(run_id).respond(UserResponse(body.request_id, body.answers))
        return {"ok": True}

    @app.get("/api/v1/workspaces/{workspace_id}/providers")
    async def providers(workspace_id: str, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        return json_value(runtime.require_providers().snapshot())

    @app.put("/api/v1/workspaces/{workspace_id}/providers/{alias}")
    async def save_provider(workspace_id: str, alias: str, body: ProviderRequest, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        service = runtime.require_providers()
        draft = body.draft()
        if draft.alias != alias:
            raise HTTPException(400, "provider alias does not match path")
        existing = service.config.providers.get(alias)
        credential_id = draft.credential_id
        if credential_id in {None, alias}:
            credential_id = (
                existing.credential_id
                if existing is not None and existing.credential_id is not None
                else f"web.{workspace_id}.{alias}"
            )
        draft = replace(
            draft,
            credential_id=credential_id,
            editing_alias=alias if existing is not None else draft.editing_alias,
        )
        result = await service.save(draft)
        await runtime.events.publish(
            {
                "type": "provider.changed",
                "workspace_id": workspace_id,
                "session_id": None,
                "run_id": None,
                "payload": {"alias": alias, "action": "saved"},
            }
        )
        return json_value(result.health)

    @app.post("/api/v1/workspaces/{workspace_id}/providers/probe")
    async def probe_provider(workspace_id: str, body: ProviderRequest, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        service = runtime.require_providers()
        draft = body.draft()
        existing = service.config.providers.get(body.alias)
        if draft.credential_id in {None, body.alias}:
            draft = replace(
                draft,
                credential_id=(
                    existing.credential_id
                    if existing is not None and existing.credential_id is not None
                    else f"web.{workspace_id}.{body.alias}"
                ),
            )
        return json_value(await runtime.require_providers().probe(draft))

    @app.put("/api/v1/workspaces/{workspace_id}/provider-chain/fallback")
    async def fallback_providers(
        workspace_id: str, body: FallbackRequest, request: Request
    ) -> dict[str, object]:
        runtime = await _runtime(request).get(workspace_id)
        config = await runtime.require_providers().set_fallback_chain(body.aliases)
        await runtime.events.publish(
            {
                "type": "provider.changed",
                "workspace_id": workspace_id,
                "session_id": None,
                "run_id": None,
                "payload": {"action": "fallback", "aliases": list(body.aliases)},
            }
        )
        return {"primary": config.primary_provider, "fallback": list(config.fallback_chain)}

    @app.post("/api/v1/workspaces/{workspace_id}/providers/{alias}/default")
    async def default_provider(workspace_id: str, alias: str, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        result = await runtime.require_providers().set_default(alias)
        await runtime.events.publish(
            {
                "type": "provider.changed",
                "workspace_id": workspace_id,
                "session_id": None,
                "run_id": None,
                "payload": {"alias": alias, "action": "default"},
            }
        )
        return json_value(result.health)

    @app.delete("/api/v1/workspaces/{workspace_id}/providers/{alias}")
    async def delete_provider(workspace_id: str, alias: str, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        await runtime.require_providers().delete(alias)
        await runtime.events.publish(
            {
                "type": "provider.changed",
                "workspace_id": workspace_id,
                "session_id": None,
                "run_id": None,
                "payload": {"alias": alias, "action": "deleted"},
            }
        )
        return {"ok": True}

    @app.delete("/api/v1/workspaces/{workspace_id}/providers/{alias}/credential")
    async def delete_provider_credential(workspace_id: str, alias: str, request: Request) -> object:
        runtime = await _runtime(request).get(workspace_id)
        health = runtime.require_providers().delete_credential(alias)
        await runtime.events.publish(
            {
                "type": "provider.changed",
                "workspace_id": workspace_id,
                "session_id": None,
                "run_id": None,
                "payload": {"alias": alias, "action": "credential-deleted"},
            }
        )
        return json_value(health)

    @app.get("/api/v1/workspaces/{workspace_id}/extensions")
    async def extensions(workspace_id: str, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        return json_value(await runtime.require_client().list_extensions())

    @app.patch("/api/v1/workspaces/{workspace_id}/extensions/{capability_id:path}")
    async def set_extension(
        workspace_id: str,
        capability_id: str,
        body: CapabilityStateRequest,
        request: Request,
    ):
        runtime = await _runtime(request).get(workspace_id)
        result = await runtime.require_client().set_extension_enabled(capability_id, body.enabled)
        if result.reload_required:
            await runtime.require_client().reload_extensions()
        await runtime.events.publish(
            {
                "type": "extensions.changed",
                "workspace_id": workspace_id,
                "session_id": None,
                "run_id": None,
                "payload": {"capability_id": capability_id, "action": "state"},
            }
        )
        return json_value(result)

    @app.post("/api/v1/workspaces/{workspace_id}/extensions/{capability_id:path}/trust")
    async def trust_extension(
        workspace_id: str,
        capability_id: str,
        body: CapabilityTrustRequest,
        request: Request,
    ):
        runtime = await _runtime(request).get(workspace_id)
        result = await runtime.require_client().trust_extension_capability(
            capability_id, body.trusted, scope=body.scope
        )
        if result.reload_required:
            await runtime.require_client().reload_extensions()
        await runtime.events.publish(
            {
                "type": "extensions.changed",
                "workspace_id": workspace_id,
                "session_id": None,
                "run_id": None,
                "payload": {"capability_id": capability_id, "action": "trust"},
            }
        )
        return json_value(result)

    @app.post("/api/v1/workspaces/{workspace_id}/plugins/install")
    async def install_plugin(workspace_id: str, body: PluginInstallRequest, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        result = await runtime.require_client().install_extension(
            Path(body.path), enable=body.enable
        )
        await runtime.require_client().reload_extensions()
        await runtime.events.publish(
            {
                "type": "extensions.changed",
                "workspace_id": workspace_id,
                "session_id": None,
                "run_id": None,
                "payload": {"action": "plugin-installed"},
            }
        )
        return json_value(result)

    @app.post("/api/v1/workspaces/{workspace_id}/skills/roots")
    async def add_skill_root(workspace_id: str, body: SkillRootRequest, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        result = await runtime.require_client().add_skill_root(
            Path(body.path), config_file=runtime.config_file
        )
        await runtime.events.publish(
            {
                "type": "extensions.changed",
                "workspace_id": workspace_id,
                "session_id": None,
                "run_id": None,
                "payload": {"action": "skill-root-added", "path": body.path},
            }
        )
        return json_value(result)

    @app.delete("/api/v1/workspaces/{workspace_id}/skills/roots")
    async def remove_skill_root(workspace_id: str, request: Request, path: Annotated[str, Query()]):
        runtime = await _runtime(request).get(workspace_id)
        result = await runtime.require_client().remove_skill_root(
            Path(path), config_file=runtime.config_file
        )
        await runtime.events.publish(
            {
                "type": "extensions.changed",
                "workspace_id": workspace_id,
                "session_id": None,
                "run_id": None,
                "payload": {"action": "skill-root-removed", "path": path},
            }
        )
        return json_value(result)

    @app.get("/api/v1/workspaces/{workspace_id}/mcp")
    async def mcp_servers(workspace_id: str, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        client = runtime.require_client()
        return {
            "servers": json_value(client.config.extensions.mcp_servers),
            "states": client.mcp_server_states(),
        }

    @app.put("/api/v1/workspaces/{workspace_id}/mcp/{server_id}")
    async def save_mcp(workspace_id: str, server_id: str, body: McpRequest, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        result = await runtime.require_client().upsert_mcp_server(
            server_id, body.config(), config_file=runtime.config_file
        )
        await runtime.events.publish(
            {
                "type": "extensions.changed",
                "workspace_id": workspace_id,
                "session_id": None,
                "run_id": None,
                "payload": {"action": "mcp-saved", "server_id": server_id},
            }
        )
        return json_value(result)

    @app.delete("/api/v1/workspaces/{workspace_id}/mcp/{server_id}")
    async def delete_mcp(workspace_id: str, server_id: str, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        result = await runtime.require_client().remove_mcp_server(
            server_id, config_file=runtime.config_file
        )
        await runtime.events.publish(
            {
                "type": "extensions.changed",
                "workspace_id": workspace_id,
                "session_id": None,
                "run_id": None,
                "payload": {"action": "mcp-deleted", "server_id": server_id},
            }
        )
        return json_value(result)

    @app.post("/api/v1/workspaces/{workspace_id}/mcp/{server_id}/probe")
    async def probe_mcp(workspace_id: str, server_id: str, request: Request):
        runtime = await _runtime(request).get(workspace_id)
        try:
            state = await runtime.require_client().probe_mcp_server(server_id)
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"state": state}

    @app.websocket("/api/v1/events")
    async def events(websocket: WebSocket, workspace_id: str, after: int = 0) -> None:
        host = websocket.url.hostname
        origin = websocket.headers.get("origin")
        if host not in {"127.0.0.1", "localhost", "::1", "testserver"} or (
            origin is not None
            and urlparse(origin).hostname not in {"127.0.0.1", "localhost", "::1"}
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            runtime = await manager.get(workspace_id)
            async for envelope in runtime.events.subscribe(after):
                await websocket.send_json(envelope)
        except (WebSocketDisconnect, RuntimeError, OSError, KeyError):
            return

    static_root = Path(__file__).with_name("static")
    if static_root.is_dir():
        assets = static_root / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend(path: str) -> FileResponse:
            if path == "api" or path.startswith("api/"):
                raise HTTPException(404, "API route not found")
            target = static_root / path
            if path and target.is_file() and target.resolve().is_relative_to(static_root.resolve()):
                return FileResponse(target)
            return FileResponse(static_root / "index.html")

    return app
