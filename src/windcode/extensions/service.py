from __future__ import annotations

import asyncio
import tomllib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from platformdirs import user_state_path
from pydantic import TypeAdapter, ValidationError

from windcode.config.models import (
    ExtensionConfig,
    McpHttpConfig,
    McpServerConfig,
    McpStdioConfig,
)
from windcode.extensions.commands import CommandRoute, build_command_catalog
from windcode.extensions.discovery import DiscoveryResult, DiscoveryRoot, discover_skills
from windcode.extensions.hooks.loader import load_hook_definition
from windcode.extensions.models import (
    ActivationState,
    CapabilityKind,
    CapabilityRecord,
    Diagnostic,
    DiagnosticSeverity,
    DiagnosticStage,
    ExtensionScope,
    ExtensionSnapshot,
    ExtensionSource,
    ManagementResult,
    PermissionRequirement,
    capability_id,
)
from windcode.extensions.paths import read_bounded
from windcode.extensions.plugins.installer import InstallResult, install_local_plugin
from windcode.extensions.plugins.manifest import (
    PluginCommand,
    PluginManifest,
    parse_plugin_manifest,
)
from windcode.extensions.skills.parser import parse_skill_metadata
from windcode.extensions.snapshot import SnapshotPublisher, build_candidate
from windcode.extensions.state import (
    ExtensionState,
    ExtensionStateStore,
    InstalledPlugin,
    ManagementAuditRecord,
)


class ExtensionService:
    def __init__(
        self,
        config: ExtensionConfig,
        workspace: Path,
        state_store: ExtensionStateStore,
        plugins_root: Path,
        *,
        user_skill_root: Path | None = None,
    ) -> None:
        self.config = config
        self.workspace = workspace.expanduser().resolve()
        self.state_store = state_store
        self.plugins_root = plugins_root
        default_user_skill_root = user_skill_root or user_state_path("windcode") / "skill"
        self._user_skill_roots = tuple(
            dict.fromkeys(
                (
                    default_user_skill_root.expanduser().resolve(),
                    *(Path(path).expanduser().resolve() for path in config.skill_roots),
                )
            )
        )
        self._project_skill_root = self.workspace / ".windcode" / "skill"
        loaded = state_store.load()
        self._state = loaded.state
        self._state_diagnostics = loaded.diagnostics
        self._snapshots = SnapshotPublisher()
        self._reload_lock = asyncio.Lock()

    @property
    def snapshot(self) -> ExtensionSnapshot:
        return self._snapshots.current

    async def list_capabilities(self) -> tuple[CapabilityRecord, ...]:
        return self.snapshot.capabilities

    @property
    def audit_records(self) -> tuple[ManagementAuditRecord, ...]:
        return () if self._state is None else self._state.audit

    def _audited(
        self,
        state: ExtensionState,
        action: str,
        source_id: str,
        status: str,
        *,
        generation: int | None = None,
    ) -> ExtensionState:
        record = ManagementAuditRecord(
            uuid4().hex,
            action,
            self.snapshot.generation if generation is None else generation,
            source_id,
            status,
            datetime.now(UTC).isoformat(),
        )
        return replace(state, audit=(*state.audit, record)[-1000:])

    async def inspect(self, identifier: str) -> tuple[CapabilityRecord, ...]:
        plugin_id = identifier.removeprefix("plugin:") if identifier.startswith("plugin:") else None
        matches = tuple(
            record
            for record in self.snapshot.capabilities
            if record.capability_id == identifier
            or record.source.source_id == identifier
            or (plugin_id is not None and record.source.plugin_id == plugin_id)
        )
        if not matches:
            raise KeyError(f"unknown extension or capability: {identifier}")
        return matches

    def command_routes(self, *, reserved: frozenset[str] = frozenset()) -> tuple[CommandRoute, ...]:
        commands: list[tuple[str, PluginCommand]] = []
        for record in self.snapshot.capabilities:
            if record.kind is not CapabilityKind.PLUGIN or not record.enabled or not record.trusted:
                continue
            definition = self.snapshot.definitions.get(record.capability_id)
            if not isinstance(definition, PluginManifest):
                continue
            source_id = f"plugin:{definition.plugin_id}"
            commands.extend((source_id, command) for command in definition.commands)
        return build_command_catalog(tuple(commands), reserved=reserved)

    async def set_enabled(self, extension_id: str, enabled: bool) -> ManagementResult:
        if self._state is None:
            return ManagementResult(False, False, self._state_diagnostics)
        previous = self._state.enabled.get(extension_id)
        if previous is enabled:
            return ManagementResult(False, False)
        values = dict(self._state.enabled)
        values[extension_id] = enabled
        plugins = dict(self._state.plugins)
        plugin_id = extension_id.removeprefix("plugin:").split("/", 1)[0]
        if plugin_id in plugins:
            plugins[plugin_id] = replace(plugins[plugin_id], enabled=enabled)
        self._state = self._audited(
            replace(self._state, enabled=values, plugins=plugins),
            "plugin_state_changed",
            extension_id,
            "enabled" if enabled else "disabled",
        )
        self.state_store.save(self._state)
        return ManagementResult(True, True)

    async def trust_workspace(self, workspace: Path, trusted: bool) -> ManagementResult:
        if self._state is None:
            return ManagementResult(False, False, self._state_diagnostics)
        before = self.state_store.is_workspace_trusted(self._state, workspace)
        if before is trusted:
            return ManagementResult(False, False)
        self._state = self._audited(
            self.state_store.set_workspace_trust(self._state, workspace, trusted),
            "workspace_trust_changed",
            "workspace",
            "trusted" if trusted else "untrusted",
        )
        self.state_store.save(self._state)
        return ManagementResult(True, True)

    async def reload(self) -> ManagementResult:
        async with self._reload_lock:
            if not self.config.enabled:
                result = DiscoveryResult((), {}, self._state_diagnostics)
            else:
                trusted = self._state is not None and self.state_store.is_workspace_trusted(
                    self._state, self.workspace
                )
                roots = [
                    DiscoveryRoot(path, ExtensionScope.USER) for path in self._user_skill_roots
                ]
                roots.append(
                    DiscoveryRoot(self._project_skill_root, ExtensionScope.PROJECT, trusted)
                )
                result = discover_skills(
                    tuple(roots), max_metadata_bytes=self.config.max_metadata_bytes
                )
                result = self._with_configured_mcp(result, project_trusted=trusted)
                result = self._with_installed_plugins(result)
            candidate = build_candidate(
                result,
                generation=self.snapshot.generation + 1,
                config={
                    **self.config.model_dump(mode="json"),
                    "_project_mcp_servers": sorted(self.config.project_mcp_servers),
                },
            )
            if self._state is not None and self.config.enabled:
                self._state = self._audited(
                    self._state,
                    "snapshot_reloaded",
                    "extension-runtime",
                    "published" if candidate.publishable else "rejected",
                    generation=candidate.snapshot.generation,
                )
                self.state_store.save(self._state)
            published = self._snapshots.publish(candidate)
            return ManagementResult(published, False, candidate.snapshot.diagnostics)

    def _with_configured_mcp(
        self, result: DiscoveryResult, *, project_trusted: bool
    ) -> DiscoveryResult:
        records = list(result.records)
        definitions = dict(result.definitions)
        for server_id, definition in sorted(self.config.mcp_servers.items()):
            project_source = server_id in self.config.project_mcp_servers
            source = ExtensionSource(
                ExtensionScope.PROJECT if project_source else ExtensionScope.USER,
                component_id=server_id,
            )
            stable_id = capability_id(CapabilityKind.MCP_SERVER, server_id)
            records.append(
                CapabilityRecord(
                    stable_id,
                    server_id,
                    CapabilityKind.MCP_SERVER,
                    source,
                    enabled=definition.enabled,
                    trusted=not project_source or project_trusted,
                    required=definition.required,
                    activation=(
                        ActivationState.INACTIVE
                        if not definition.enabled
                        else (
                            ActivationState.AVAILABLE
                            if not project_source or project_trusted
                            else ActivationState.UNTRUSTED
                        )
                    ),
                    permissions=PermissionRequirement(
                        network=definition.transport == "streamable_http",
                        process=definition.transport == "stdio",
                    ),
                )
            )
            definitions[stable_id] = definition
        return DiscoveryResult(tuple(records), definitions, result.diagnostics)

    def _with_installed_plugins(self, result: DiscoveryResult) -> DiscoveryResult:
        if self._state is None:
            return DiscoveryResult(
                result.records,
                result.definitions,
                (*result.diagnostics, *self._state_diagnostics),
            )
        records = list(result.records)
        definitions = dict(result.definitions)
        diagnostics = list(result.diagnostics)
        for installed in sorted(self._state.plugins.values(), key=lambda item: item.plugin_id):
            root = self.plugins_root / installed.plugin_id / installed.digest
            source = ExtensionSource(
                ExtensionScope.USER, root, installed.plugin_id, digest=installed.digest
            )
            try:
                self._add_plugin(
                    installed.plugin_id,
                    installed.enabled,
                    root,
                    source,
                    records,
                    definitions,
                )
            except (
                OSError,
                UnicodeError,
                ValueError,
                tomllib.TOMLDecodeError,
                ValidationError,
            ) as exc:
                diagnostic = Diagnostic(
                    DiagnosticStage.PARSE,
                    DiagnosticSeverity.ERROR,
                    "invalid_plugin",
                    str(exc),
                    source.source_id,
                    "Repair or reinstall the plugin.",
                )
                diagnostics.append(diagnostic)
                records.append(
                    CapabilityRecord(
                        capability_id(
                            CapabilityKind.PLUGIN,
                            installed.plugin_id,
                            plugin_id=installed.plugin_id,
                        ),
                        installed.plugin_id,
                        CapabilityKind.PLUGIN,
                        source,
                        enabled=installed.enabled,
                        required=True,
                        activation=ActivationState.FAILED,
                        diagnostics=(diagnostic,),
                    )
                )
        return DiscoveryResult(
            tuple(sorted(records, key=lambda item: item.sort_key)),
            definitions,
            tuple(sorted(diagnostics, key=lambda item: (item.source_id, item.category))),
        )

    def _add_plugin(
        self,
        plugin_id: str,
        enabled: bool,
        root: Path,
        source: ExtensionSource,
        records: list[CapabilityRecord],
        definitions: dict[str, object],
    ) -> None:
        manifest = parse_plugin_manifest(root, max_bytes=self.config.max_metadata_bytes)
        plugin_stable_id = capability_id(CapabilityKind.PLUGIN, plugin_id, plugin_id=plugin_id)
        activation = ActivationState.AVAILABLE if enabled else ActivationState.INACTIVE
        records.append(
            CapabilityRecord(
                plugin_stable_id,
                plugin_id,
                CapabilityKind.PLUGIN,
                source,
                enabled=enabled,
                required=manifest.required,
                activation=activation,
            )
        )
        definitions[plugin_stable_id] = manifest
        for component in manifest.skills:
            component_source = replace(source, component_id=component.component_id)
            stable_id = capability_id(
                CapabilityKind.SKILL, component.component_id, plugin_id=plugin_id
            )
            metadata = parse_skill_metadata(
                root / component.path, max_bytes=self.config.max_metadata_bytes
            )
            records.append(
                CapabilityRecord(
                    stable_id,
                    metadata.name,
                    CapabilityKind.SKILL,
                    component_source,
                    enabled=enabled,
                    required=manifest.required,
                    activation=activation,
                    permissions=PermissionRequirement(filesystem_read=True),
                )
            )
            definitions[stable_id] = metadata
        for component in manifest.hooks:
            component_source = replace(source, component_id=component.component_id)
            stable_id = capability_id(
                CapabilityKind.HOOK, component.component_id, plugin_id=plugin_id
            )
            hook = load_hook_definition(
                root,
                component.path,
                source_id=component_source.source_id,
                max_bytes=self.config.max_metadata_bytes,
            )
            records.append(
                CapabilityRecord(
                    stable_id,
                    component.component_id,
                    CapabilityKind.HOOK,
                    component_source,
                    enabled=enabled,
                    required=manifest.required or hook.required,
                    activation=activation,
                )
            )
            definitions[stable_id] = hook
        for component in manifest.mcp_servers:
            component_source = replace(source, component_id=component.component_id)
            stable_id = capability_id(
                CapabilityKind.MCP_SERVER, component.component_id, plugin_id=plugin_id
            )
            raw = tomllib.loads(
                read_bounded(root, component.path, max_bytes=self.config.max_metadata_bytes).decode(
                    "utf-8"
                )
            )
            server = cast(
                McpStdioConfig | McpHttpConfig,
                TypeAdapter(McpServerConfig).validate_python(raw),
            )
            server_enabled = enabled and server.enabled
            records.append(
                CapabilityRecord(
                    stable_id,
                    component.component_id,
                    CapabilityKind.MCP_SERVER,
                    component_source,
                    enabled=server_enabled,
                    required=manifest.required or server.required,
                    activation=(activation if server_enabled else ActivationState.INACTIVE),
                    permissions=PermissionRequirement(
                        network=server.transport == "streamable_http",
                        process=server.transport == "stdio",
                    ),
                )
            )
            definitions[stable_id] = server

    async def install_local(self, source: Path, *, enable: bool = False) -> InstallResult:
        if self._state is None:
            raise ValueError("extension state is corrupt")
        result = install_local_plugin(source, self.plugins_root)
        plugins = dict(self._state.plugins)
        existing = plugins.get(result.manifest.plugin_id)
        if existing is not None and existing.digest == result.digest:
            if not enable or existing.enabled:
                return result
            plugins[result.manifest.plugin_id] = replace(existing, enabled=True)
            self._state = self._audited(
                replace(self._state, plugins=plugins),
                "plugin_state_changed",
                f"plugin:{result.manifest.plugin_id}",
                "enabled",
            )
            self.state_store.save(self._state)
            return result
        plugins[result.manifest.plugin_id] = InstalledPlugin(
            result.manifest.plugin_id,
            result.manifest.version,
            result.digest,
            source.name,
            datetime.now(UTC).isoformat(),
            enable,
        )
        self._state = self._audited(
            replace(self._state, plugins=plugins),
            "plugin_installed",
            f"plugin:{result.manifest.plugin_id}",
            "enabled" if enable else "disabled",
        )
        self.state_store.save(self._state)
        return result
