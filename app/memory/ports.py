from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class MemoryRecord:
    id: str
    content: str
    memory_type: str
    source: str
    trust_level: str
    state: str


class MemoryPort(Protocol):
    async def retrieve(
        self, *, user_id: str, session_id: str, query: str, limit: int = 6
    ) -> list[MemoryRecord]: ...
    async def remember_preference(
        self, *, user_id: str, session_id: str, content: str, source: str
    ) -> MemoryRecord: ...
