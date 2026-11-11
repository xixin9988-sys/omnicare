import asyncio
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from app.memory.ports import MemoryRecord


class LocalFileMemoryAdapter:
    """Runnable adapter for local demos; not a replacement for NanoMemory's hybrid retriever."""

    def __init__(self, storage_dir: str = "data/memory") -> None:
        self.root = Path(storage_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _path(self, user_id: str) -> Path:
        # Hash the identifier: sanitising is not isolation, because two ids can sanitise to
        # the same name (``alice@example.com`` vs ``alice_example.com``).
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]
        return self.root / f"{digest}.jsonl"

    async def retrieve(
        self, *, user_id: str, session_id: str, query: str, limit: int = 6
    ) -> list[MemoryRecord]:
        path = self._path(user_id)
        if not path.exists():
            return []
        terms = {x for x in re.findall(r"[\w\u4e00-\u9fff]+", query.lower()) if len(x) > 1}
        found: list[tuple[int, MemoryRecord]] = []
        async with self._lock:
            lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            try:
                item = json.loads(line)
                if item.get("state", "ACTIVE") != "ACTIVE":
                    continue
                content = str(item.get("content", ""))
                score = sum(1 for term in terms if term in content.lower())
                if score or item.get("memory_type") == "preference":
                    found.append(
                        (
                            score,
                            MemoryRecord(
                                id=str(item["id"]),
                                content=content,
                                memory_type=str(item.get("memory_type", "semantic")),
                                source=str(item.get("source", "unknown")),
                                trust_level=str(item.get("trust_level", "unknown")),
                                state=str(item.get("state", "ACTIVE")),
                            ),
                        )
                    )
            except (ValueError, KeyError, TypeError):
                # A malformed local demo line is skipped; production NanoMemory owns its quarantine path.
                continue
        found.sort(key=lambda pair: pair[0], reverse=True)
        return [record for _, record in found[:limit]]

    async def remember_preference(
        self, *, user_id: str, session_id: str, content: str, source: str
    ) -> MemoryRecord:
        record = MemoryRecord(
            id=str(uuid4()),
            content=content,
            memory_type="preference",
            source=source,
            trust_level="user_asserted",
            state="ACTIVE",
        )
        payload = {
            "id": record.id,
            "session_id": session_id,
            "content": content,
            "memory_type": record.memory_type,
            "source": source,
            "trust_level": record.trust_level,
            "state": record.state,
        }
        async with self._lock:
            with self._path(user_id).open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return record
