from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from windcode.extensions.models import (
    ActivationState,
    CapabilityKind,
    CapabilityRecord,
    Diagnostic,
    DiagnosticSeverity,
    DiagnosticStage,
    ExtensionScope,
    ExtensionSource,
    PermissionRequirement,
    capability_id,
)
from windcode.extensions.skills.parser import parse_skill_metadata


@dataclass(frozen=True, slots=True)
class DiscoveryRoot:
    path: Path
    scope: ExtensionScope
    trusted: bool = True


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    records: tuple[CapabilityRecord, ...]
    definitions: dict[str, Any]
    diagnostics: tuple[Diagnostic, ...]


def discover_skills(
    roots: tuple[DiscoveryRoot, ...], *, max_metadata_bytes: int = 65_536
) -> DiscoveryResult:
    candidates: list[tuple[CapabilityRecord, Any]] = []
    diagnostics: list[Diagnostic] = []
    for root in sorted(roots, key=lambda item: (item.scope.value, str(item.path))):
        if not root.path.exists():
            continue
        for directory in sorted(
            (path for path in root.path.iterdir() if path.is_dir()), key=lambda path: path.name
        ):
            source = ExtensionSource(root.scope, directory)
            try:
                metadata = parse_skill_metadata(directory, max_bytes=max_metadata_bytes)
                stable_id = capability_id(CapabilityKind.SKILL, metadata.name)
                record = CapabilityRecord(
                    stable_id,
                    metadata.name,
                    CapabilityKind.SKILL,
                    source,
                    trusted=root.trusted,
                    activation=ActivationState.AVAILABLE
                    if root.trusted
                    else ActivationState.INACTIVE,
                    permissions=PermissionRequirement(filesystem_read=True),
                )
                candidates.append((record, metadata))
            except (OSError, ValueError) as exc:
                diagnostics.append(
                    Diagnostic(
                        DiagnosticStage.PARSE,
                        DiagnosticSeverity.ERROR,
                        "invalid_skill",
                        str(exc),
                        source.source_id,
                        "Fix the Skill metadata or remove this source.",
                    )
                )

    grouped: dict[tuple[CapabilityKind, str], list[tuple[CapabilityRecord, Any]]] = {}
    for candidate in candidates:
        record = candidate[0]
        grouped.setdefault((record.kind, record.public_name), []).append(candidate)
    records: list[CapabilityRecord] = []
    definitions: dict[str, Any] = {}
    for key in sorted(grouped, key=lambda item: (item[0].value, item[1])):
        items = sorted(grouped[key], key=lambda item: item[0].sort_key)
        by_scope: dict[ExtensionScope, list[tuple[CapabilityRecord, Any]]] = {}
        for item in items:
            by_scope.setdefault(item[0].source.scope, []).append(item)
        conflicted = {scope for scope, values in by_scope.items() if len(values) > 1}
        eligible = [item for item in items if item[0].source.scope not in conflicted]
        winner = eligible[-1] if eligible else None
        for item in items:
            record, definition = item
            if record.source.scope in conflicted:
                diagnostic = Diagnostic(
                    DiagnosticStage.MERGE,
                    DiagnosticSeverity.ERROR,
                    "same_scope_conflict",
                    f"duplicate {record.kind.value} named {record.public_name}",
                    record.source.source_id,
                    "Remove or rename one same-scope definition.",
                )
                diagnostics.append(diagnostic)
                records.append(
                    CapabilityRecord(
                        record.capability_id,
                        record.public_name,
                        record.kind,
                        record.source,
                        record.enabled,
                        record.trusted,
                        record.required,
                        ActivationState.FAILED,
                        record.permissions,
                        None,
                        (diagnostic,),
                    )
                )
            elif winner is not None and record is not winner[0]:
                records.append(
                    CapabilityRecord(
                        record.capability_id,
                        record.public_name,
                        record.kind,
                        record.source,
                        record.enabled,
                        record.trusted,
                        record.required,
                        ActivationState.INACTIVE,
                        record.permissions,
                        winner[0].source.source_id,
                        record.diagnostics,
                    )
                )
            else:
                records.append(record)
                definitions[record.capability_id] = definition
    return DiscoveryResult(
        tuple(sorted(records, key=lambda item: item.sort_key)),
        definitions,
        tuple(sorted(diagnostics, key=lambda item: (item.source_id, item.category))),
    )
