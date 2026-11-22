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

import asyncio

import httpx

from app.core.config import Settings
from app.memory.ports import MemoryRecord, normalize_provenance

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
        provenance=normalize_provenance(full.get("provenance") or item.get("provenance")),
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
        # NanoMemory sessions are server-generated (spec 24.3): the OmniCare session id is
        # not a NanoMemory session id. Probe the deployment mode once, then either omit the
        # header (single-session deployment) or create and cache a NanoMemory session per
        # OmniCare conversation (multi-session deployment).
        self._session_mode: str | None = None
        self._nanomemory_sessions: dict[str, str] = {}
        self._session_lock = asyncio.Lock()

    def _base_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _client(self, headers: dict[str, str]) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers=headers,
            transport=self.transport,
        )

    async def _ensure_session_mode(self) -> str:
        """Return "single" or "multi" for this NanoMemory deployment.

        GET /api/v1/version is public. If the probe fails (older server or contract-test
        transport), fall back to single-session behaviour: omitting the header is the safe
        choice because NanoMemory uses its default session instead of rejecting an unknown id.
        """
        if self._session_mode is None:
            async with self._session_lock:
                if self._session_mode is None:
                    mode = "single"
                    try:
                        async with self._client(self._base_headers()) as client:
                            response = await client.get("/api/v1/version")
                        if response.status_code == 200:
                            candidate = str(response.json().get("session_mode") or "").lower()
                            if candidate in {"single", "multi"}:
                                mode = candidate
                    except (httpx.HTTPError, ValueError):
                        mode = "single"
                    self._session_mode = mode
        return self._session_mode

    async def _session_headers(self, session_id: str) -> dict[str, str]:
        if await self._ensure_session_mode() == "single":
            return {}
        cached = self._nanomemory_sessions.get(session_id)
        if cached:
            return {"X-NanoMemory-Session-ID": cached}
        async with self._session_lock:
            cached = self._nanomemory_sessions.get(session_id)
            if cached:
                return {"X-NanoMemory-Session-ID": cached}
            async with self._client(self._base_headers()) as client:
                response = await client.post("/api/v1/sessions")
                response.raise_for_status()
                created = str(response.json().get("session_id") or "")
            if not created:
                raise RuntimeError("NanoMemory did not return a session id")
            self._nanomemory_sessions[session_id] = created
            return {"X-NanoMemory-Session-ID": created}

    async def _request_headers(self, session_id: str) -> dict[str, str]:
        headers = self._base_headers()
        headers.update(await self._session_headers(session_id))
        return headers

    async def _open(self, session_id: str) -> httpx.AsyncClient:
        return self._client(await self._request_headers(session_id))

    @staticmethod
    async def _records(client: httpx.AsyncClient, limit: int = 500) -> dict[str, dict]:
        response = await client.get("/api/v1/memory/long-term", params={"limit": limit})
        if response.status_code != 200:
            return {}
        return {str(row.get("id")): row for row in response.json().get("records", [])}

    async def retrieve(
        self, *, user_id: str, session_id: str, query: str, limit: int = 6
    ) -> list[MemoryRecord]:
        async with await self._open(session_id) as client:
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
        self,
        *,
        user_id: str,
        session_id: str,
        content: str,
        source: str,
        source_ref: str = "",
        created_by: str = "omnicare",
    ) -> MemoryRecord:
        # The provenance object is the server-side half of the audit chain: source_kind says
        # the bytes came from a user turn, source_ref points at the OmniCare session/run.
        body: dict = {
            "content": content,
            "category": "preference",
            "source": source,
            "source_type": "user",
            "user_id": user_id,
            "tags": ["omnicare", "preference"],
        }
        # Only send provenance when a source reference exists: that keeps the adapter
        # compatible with deployments whose create DTO predates the provenance field.
        if source_ref:
            body["provenance"] = {
                "source_kind": "user",
                "source_ref": source_ref,
                "created_by": created_by,
            }
        self.last_request = dict(body)
        async with await self._open(session_id) as client:
            response = await client.post("/api/v1/memory/long-term", json=body)
            response.raise_for_status()
            created = response.json()
            # POST returns the full record; trust is derived from source_type by contract.
            record = _to_record(created, created)
        if record.trust_level == "unknown":
            record.trust_level = "user_stated"
        return record
