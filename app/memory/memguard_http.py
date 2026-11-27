"""Memory adapter that goes through mem-guard, never straight to the memory engine.

Why the extra hop matters (and is not decoration):

* **Tenant proof.** mem-guard signs tenant/user/ACL into an HMAC envelope that NanoMemory
  verifies before building its SQL pre-filter. A direct adapter would have to hold that key in
  the web process; this one holds only the gateway's service key.
* **Pre-write screening.** PII/secrets are redacted and prompt injection is blocked before the
  text is embedded, so a leaked key never becomes a vector or a trace payload.
* **Cost control.** Rate limits and daily budgets are enforced per tenant/user, with typed 429s.
* **Trace ids.** Every recall returns the trace id that the UI sends back with thumbs up/down.

Failures are wrapped in MemoryBackendError so the supervisor can degrade (answer without
memory) instead of failing the whole chat when the gateway is down.
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.memory.ports import MemoryBackendError, MemoryRecord, normalize_provenance


def _record_from_guard(payload: dict) -> MemoryRecord:
    metadata = payload.get("metadata") or {}
    return MemoryRecord(
        id=str(payload.get("id") or "unknown"),
        content=str(payload.get("content") or ""),
        memory_type=str(metadata.get("memory_type") or "fact"),
        source=str(metadata.get("source") or "memguard"),
        trust_level=str(metadata.get("trust_level") or "unknown"),
        state="ACTIVE",
        provenance=normalize_provenance(metadata.get("provenance")),
    )


class MemGuardMemoryAdapter:
    """Implements MemoryPort against mem-guard's /v1/guard API."""

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = settings.memguard_base_url.rstrip("/")
        self.service_key = settings.memguard_service_key
        self.timeout = settings.memguard_timeout_seconds
        self.transport = transport
        self.tenant_id_default = settings.default_tenant_id
        self.last_trace_id: str | None = None
        self.last_guard: dict | None = None
        self.last_error: str | None = None

    def _headers(self, *, user_id: str, tenant_id: str) -> dict[str, str]:
        headers = {"X-Guard-Tenant": tenant_id, "X-Guard-User": user_id}
        if self.service_key:
            headers["Authorization"] = f"Bearer {self.service_key}"
        return headers

    async def _post(
        self,
        path: str,
        *,
        user_id: str,
        tenant_id: str,
        payload: dict,
    ) -> dict:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.post(
                    path,
                    headers=self._headers(user_id=user_id, tenant_id=tenant_id),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise MemoryBackendError(
                "memory_gateway_unavailable", "The memory gateway is unreachable."
            ) from exc
        if response.status_code >= 400:
            body = _safe_json(response)
            raise MemoryBackendError(
                str(body.get("code") or "memory_gateway_error"),
                str(body.get("message") or "The memory gateway rejected the request."),
                status=response.status_code,
            )
        return _safe_json(response)

    async def retrieve(
        self,
        *,
        user_id: str,
        session_id: str,
        query: str,
        limit: int = 6,
        tenant_id: str = "default",
    ) -> list[MemoryRecord]:
        payload = {
            "query": query,
            "limit": max(1, min(limit, 20)),
            "min_trust": "unknown",
        }
        body = await self._post(
            "/v1/guard/recall",
            user_id=user_id,
            tenant_id=tenant_id or self.tenant_id_default,
            payload=payload,
        )
        self.last_trace_id = str(body.get("trace_id") or "") or None
        self.last_guard = body.get("guard") or {}
        if body.get("insufficient_context"):
            return []
        return [_record_from_guard(item) for item in body.get("items", [])]

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
    ) -> MemoryRecord:
        payload = {
            "content": content,
            "category": "preference",
            "memory_type": "preference",
            "confidence": "high",
            "source_kind": "user",
            "source_ref": source_ref or None,
            "attributes": {"omnicare_source": source, "created_by": created_by},
        }
        body = await self._post(
            "/v1/guard/remember",
            user_id=user_id,
            tenant_id=tenant_id or self.tenant_id_default,
            payload=payload,
        )
        self.last_trace_id = str(body.get("trace_id") or "") or self.last_trace_id
        return _record_from_guard(body.get("record") or {})

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.get("/healthz")
            return _safe_json(response)
        except httpx.HTTPError as exc:
            raise MemoryBackendError(
                "memory_gateway_unavailable", "The memory gateway is unreachable."
            ) from exc


def _safe_json(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


__all__ = ["MemGuardMemoryAdapter"]
