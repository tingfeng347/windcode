from __future__ import annotations

from textual.widgets import Static

from windcode.extensions.models import CapabilityRecord


class ExtensionList(Static):
    """Responsive textual representation of the shared extension catalog."""

    DEFAULT_CSS = """
    ExtensionList {
        width: 100%;
        height: auto;
        max-height: 16;
        overflow-y: auto;
        padding: 0 2;
    }
    """

    @staticmethod
    def render_text(records: tuple[CapabilityRecord, ...]) -> str:
        lines = ["扩展能力:"]
        for record in records:
            state = record.activation.value
            trust = "trusted" if record.trusted else "untrusted"
            permissions = [
                name
                for name, required in (
                    ("read", record.permissions.filesystem_read),
                    ("write", record.permissions.filesystem_write),
                    ("network", record.permissions.network),
                    ("process", record.permissions.process),
                )
                if required
            ]
            lines.append(
                f"{record.capability_id}\n  {record.kind.value} · {record.source.scope.value} · "
                f"{state} · {trust} · permissions={','.join(permissions) or 'none'}"
            )
            lines.extend(
                f"  diagnostic={diagnostic.category}: {diagnostic.message}"
                for diagnostic in record.diagnostics
            )
        return "\n".join(lines) if records else "扩展能力:\n未发现扩展能力"

    def __init__(self, records: tuple[CapabilityRecord, ...]) -> None:
        super().__init__(self.render_text(records))
