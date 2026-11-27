from dataclasses import dataclass, field
from typing import Protocol


class MemoryBackendError(RuntimeError):
    """A memory backend refused or could not serve the request (typed for degradation)."""

    def __init__(self, code: str, message: str, *, status: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(slots=True)
class MemoryRecord:
    id: str
    content: str
    memory_type: str
    source: str
    trust_level: str
    state: str
    # Audit trail fields (source_kind/source_ref/created_by) captured when the memory was
    # written. Empty for records produced before provenance was tracked or by a backend that
    # does not return it.
    provenance: dict[str, str] = field(default_factory=dict)


def normalize_provenance(raw: object) -> dict[str, str]:
    """Coerce a backend provenance payload to the auditable dict[str, str] shape.

    Backends may return None, nested lists or null fields (NanoMemory reports
    source_ref: null and derived_from: []); only string values are meaningful here.
    """
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, str)}


class MemoryPort(Protocol):
    async def retrieve(
        self,
        *,
        user_id: str,
        session_id: str,
        query: str,
        limit: int = 6,
        tenant_id: str = "default",
    ) -> list[MemoryRecord]: ...

    async def remember_preference(
        self,
        *,
        user_id: str,
        session_id: str,
        content: str,
        source: str,
        source_ref: str = "",
        created_by: str = "omnicare",
        tenant_id: str = "default",
    ) -> MemoryRecord: ...
