"""Public types used by the SDK facade without exposing implementation package dependencies."""

from windcode.domain.events import RunRequest
from windcode.domain.messages import Message
from windcode.domain.tools import Tool
from windcode.extensions.commands import CommandRoute
from windcode.extensions.models import CapabilityRecord, ExtensionSnapshot, ManagementResult
from windcode.extensions.plugins.installer import InstallResult
from windcode.extensions.service import ExtensionService
from windcode.extensions.skills.tools import SkillSearchResult
from windcode.extensions.state import ManagementAuditRecord
from windcode.memory import (
    MemoryActivation,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryService,
    MemorySource,
    MemoryStatus,
)
from windcode.providers import ModelTransport, TransportRegistry
from windcode.runtime.run_handle import RunHandle
from windcode.sessions import EventRecord, SessionMetadata
from windcode.tools import ToolRegistry

__all__ = [
    "CapabilityRecord",
    "CommandRoute",
    "EventRecord",
    "ExtensionService",
    "ExtensionSnapshot",
    "InstallResult",
    "ManagementAuditRecord",
    "ManagementResult",
    "MemoryActivation",
    "MemoryKind",
    "MemoryRecord",
    "MemoryScope",
    "MemoryService",
    "MemorySource",
    "MemoryStatus",
    "Message",
    "ModelTransport",
    "RunHandle",
    "RunRequest",
    "SessionMetadata",
    "SkillSearchResult",
    "Tool",
    "ToolRegistry",
    "TransportRegistry",
]
