"""HTTP adapter for the **real** NanoMemory REST API (NanoMemory 0.2.0).

Contract captured from the running service into ``tests/fixtures/nanomemory_contract.json``:

* ``GET  /api/v1/memory/recall?q=&limit=&user_id=`` → ``{items: [{id, kind, content,
  memory_type, status, signals}], intent, insufficient_context, ...}``
* ``GET  /api/v1/memory/long-term?limit=`` → ``{count, records: [<full record>]}`` where a full
  record carries ``trust_level``, ``status``, ``source_type`` and ``provenance``.
* ``POST /api/v1/memory/long-term`` → the full record (no trust label is accepted; see
  ``_map_trust``).

Two lookups are used on purpose: recall is the governed retrieval path, but it does not return
the trust label yet, so the adapter enriches hits from the long-term listing. That is an
explicit, documented contract gap rather than a guess.
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.memory.ports import MemoryRecord

_TRUST_MAP = {
    "user_provided": "user_stated",
    "user_asserted": "user_stated",
    "verified": "user_verified",
    "untrusted": "untrusted",
    "model_inferred": "model_inferred",
    "unknown": "unknown",
}


def _map_trust(record: dict) -> str:
    """Map NanoMemory trust to OmniCare vocabulary, using ``source_type`` as attestation.

    NanoMemory's long-term create endpoint does not accept a trust label, so a record written
    by this adapter with ``source_type="user"`` is the adapter's own attestation that the bytes
    came from a user turn, not the model.
    """
    explicit = str(record.get("trust_level") or "unknown").lower()
    if explicit in _TRUST_MAP and explicit != "unknown":
        return _TRUST_MAP[explicit]
    source_type = str(record.get("source_type") or "").lower()
    if source_type == "user":
        return "user_stated"
    provenance = record.get("provenance") or {}
    if str(provenance.get("source_kind") or "").lower() == "user":
        return "user_stated"
    return "unknown"


def _to_record(item: dict, full: dict | None = None) -> MemoryRecord:
    full = full or {}
    return MemoryRecord(
        id=str(item.get("id") or full.get("id") or "unknown"),
        content=str(item.get("content") or full.get("content") or ""),
        memory_type=str(item.get("memory_type") or full.get("memory_type") or "fact"),
        source=str(full.get("source") or item.get("source") or "nanomemory"),
        trust_level=_map_trust(full or item),
        state=str(item.get("status") or full.get("status") or "unknown").upper(),
    )


class NanoMemoryHTTPAdapter:
    """Aligned with the deployed NanoMemory routes, with optional transport for contract tests."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = settings.nanomemory_base_url.rstrip("/")
        self.api_key = settings.nanomemory_api_key
        self.timeout = 8.0
        self.transport = transport
        self.last_request: dict | None = None

    def _headers(self, session_id: str) -> dict[str, str]:
        headers = {"X-NanoMemory-Session-ID": session_id}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _client(self, session_id: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers=self._headers(session_id),
            transport=self.transport,
        )

    @staticmethod
    async def _records(client: httpx.AsyncClient, limit: int = 500) -> dict[str, dict]:
        response = await client.get("/api/v1/memory/long-term", params={"limit": limit})
        if response.status_code != 200:
            return {}
        return {str(row.get("id")): row for row in response.json().get("records", [])}

    async def retrieve(
        self, *, user_id: str, session_id: str, query: str, limit: int = 6
    ) -> list[MemoryRecord]:
        async with self._client(session_id) as client:
            response = await client.get(
                "/api/v1/memory/recall",
                params={"q": query, "limit": limit, "user_id": user_id},
            )
            response.raise_for_status()
            payload = response.json()
            records = await self._records(client)
        items = [item for item in payload.get("items", []) if item.get("kind") == "long_term"]
        return [_to_record(item, records.get(str(item.get("id")))) for item in items][:limit]

    async def remember_preference(
        self, *, user_id: str, session_id: str, content: str, source: str
    ) -> MemoryRecord:
        body = {
            "content": content,
            "category": "preference",
            "source": source,
            "source_type": "user",
            "tags": ["omnicare", "preference"],
        }
        self.last_request = dict(body)
        async with self._client(session_id) as client:
            response = await client.post("/api/v1/memory/long-term", json=body)
            response.raise_for_status()
            created = response.json()
            # POST returns the full record; trust is derived from source_type by contract.
            record = _to_record(created, created)
        if record.trust_level == "unknown":
            record.trust_level = "user_stated"
        return record
