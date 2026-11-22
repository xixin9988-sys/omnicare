"""End-to-end memory provenance: attestation on write, verification on read, admin audit API.

Chat runs through the local memory adapter (never a live NanoMemory). The adapter contract
cases below use mocked HTTP transports so they stay deterministic and server-independent.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.api.deps import Principal, require_admin
from app.core.config import Settings, get_settings
from app.core.db import SessionLocal
from app.governance.local_guard import LocalPolicyGuard
from app.main import app
from app.memory.nanomemory_http import NanoMemoryHTTPAdapter
from app.memory.ports import MemoryRecord
from app.models.entities import AgentEvent, GovernanceIncident, MemoryProvenance
from app.services.memory_provenance import (
    REASON_INTEGRITY,
    REASON_SOURCE,
    REASON_VERIFIED,
    attestation_digest,
    resolve_attestation_secret,
    verify_memory_provenance,
)

PREFERENCE_MESSAGE = "以后不要用顺丰，优先用京东物流"


@pytest.fixture()
def admin_override():
    app.dependency_overrides[require_admin] = lambda: Principal(user_id="admin-test", role="admin")
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_admin, None)


def _unique_user() -> str:
    return f"prov-{uuid4().hex[:12]}"


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


async def _session(client, user_id: str) -> str:
    created = await client.post(
        "/api/v1/sessions", json={"user_id": user_id}, headers=_headers(user_id)
    )
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


async def _chat(client, user_id: str, session_id: str, message: str) -> dict:
    response = await client.post(
        "/api/v1/chat",
        json={"user_id": user_id, "session_id": session_id, "message": message},
        headers=_headers(user_id),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _memory_record_for(row: MemoryProvenance) -> MemoryRecord:
    return MemoryRecord(
        id=row.memory_id,
        content=PREFERENCE_MESSAGE,
        memory_type="preference",
        source="user_explicit_statement",
        trust_level="user_asserted",
        state="ACTIVE",
        provenance={"source_kind": "user", "source_ref": row.source_ref, "created_by": "omnicare"},
    )


@pytest.mark.asyncio
async def test_explicit_preference_writes_verified_attestation(client, admin_override):
    user_id = _unique_user()
    session_id = await _session(client, user_id)
    payload = await _chat(client, user_id, session_id, PREFERENCE_MESSAGE)
    assert payload["status"] == "completed"
    run_id = payload["run_id"]
    secret = resolve_attestation_secret(get_settings())

    async with SessionLocal() as db:
        row = await db.scalar(select(MemoryProvenance).where(MemoryProvenance.user_id == user_id))
        event = await db.scalar(
            select(AgentEvent).where(
                AgentEvent.run_id == run_id, AgentEvent.event_type == "memory_written"
            )
        )
        assert row is not None
        assert event is not None
        assert row.user_id == user_id
        assert row.session_id == session_id
        assert row.run_id == run_id
        assert row.source_ref == f"omnicare://sessions/{session_id}/runs/{run_id}"
        assert row.backend == "local"
        assert row.source_type == "user"
        assert row.created_by == "omnicare"
        assert row.issuer == "omnicare"
        assert row.tenant_id == "default"
        assert row.integrity == attestation_digest(
            secret,
            memory_id=row.memory_id,
            backend=row.backend,
            user_id=row.user_id,
            session_id=row.session_id,
            run_id=row.run_id,
            source_ref=row.source_ref,
        )
        assert event.payload["memory_id"] == row.memory_id
        assert event.payload["source_ref"] == row.source_ref
        # The HMAC key must never ride along in an event payload.
        assert "secret" not in json.dumps(event.payload, ensure_ascii=False).lower()
        memory_id = row.memory_id
        source_ref = row.source_ref

    audit = await client.get(f"/api/v1/memories/{memory_id}/provenance")
    assert audit.status_code == 200, audit.text
    body = audit.json()
    assert body["id"] == memory_id
    assert body["source_ref"] == source_ref
    assert body["created_by"] == "omnicare"
    assert body["backend"] == "local"
    assert body["session_id"] == session_id
    assert body["run_id"] == run_id
    assert isinstance(body["created_at"], str) and body["created_at"]
    assert body["verified"] is True


@pytest.mark.asyncio
async def test_tampered_attestation_is_not_verified(client, admin_override):
    user_id = _unique_user()
    session_id = await _session(client, user_id)
    await _chat(client, user_id, session_id, PREFERENCE_MESSAGE)
    secret = resolve_attestation_secret(get_settings())

    async with SessionLocal() as db:
        row = await db.scalar(select(MemoryProvenance).where(MemoryProvenance.user_id == user_id))
        assert row is not None
        memory_id = row.memory_id
        record = await _memory_record_for(row)
        assert await verify_memory_provenance(db, memory=record, secret=secret) == (
            True,
            REASON_VERIFIED,
        )

        # A record whose provenance points at a different conversation is not bound to the row.
        foreign = await _memory_record_for(row)
        foreign.provenance["source_ref"] = "omnicare://sessions/other/runs/other"
        assert await verify_memory_provenance(db, memory=foreign, secret=secret) == (
            False,
            REASON_SOURCE,
        )

        # Tampering the stored digest breaks the HMAC check.
        row.integrity = "0" * 64
        await db.commit()
        assert await verify_memory_provenance(db, memory=record, secret=secret) == (
            False,
            REASON_INTEGRITY,
        )

    audit = await client.get(f"/api/v1/memories/{memory_id}/provenance")
    assert audit.status_code == 200, audit.text
    assert audit.json()["verified"] is False


@pytest.mark.asyncio
async def test_provenance_endpoint_404_for_unknown_memory(client, admin_override):
    response = await client.get("/api/v1/memories/does-not-exist/provenance")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "memory_provenance_not_found"


@pytest.mark.asyncio
async def test_provenance_endpoint_requires_admin(client):
    response = await client.get(
        "/api/v1/memories/any-memory/provenance", headers=_headers("demo_user_001")
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "admin_required"


def test_local_guard_rejects_user_stated_memory_without_source_ref():
    guard = LocalPolicyGuard()
    memory = SimpleNamespace(
        state="ACTIVE",
        source="user_explicit_statement",
        trust_level="user_asserted",
        provenance={"source_kind": "user", "created_by": "omnicare"},
    )
    decision = guard.validate_memory(memory)
    assert decision.allowed is False
    assert decision.decision == "quarantine"
    assert decision.reason_code == "memory_provenance_missing"

    memory.provenance["source_ref"] = "omnicare://sessions/s1/runs/r1"
    assert guard.validate_memory(memory).allowed is True

    # Objects that predate the provenance field keep the previous behavior (legacy adapters).
    legacy = SimpleNamespace(
        state="ACTIVE", source="user_explicit_statement", trust_level="user_asserted"
    )
    assert guard.validate_memory(legacy).allowed is True


@pytest.mark.asyncio
async def test_supervisor_written_memory_is_trusted_on_next_retrieval(client):
    user_id = _unique_user()
    session_id = await _session(client, user_id)
    await _chat(client, user_id, session_id, PREFERENCE_MESSAGE)

    second = await _chat(client, user_id, session_id, "我的订单物流到哪了？")
    assert second["status"] == "completed"
    async with SessionLocal() as db:
        incidents = (
            await db.scalars(
                select(GovernanceIncident).where(
                    GovernanceIncident.run_id == second["run_id"],
                    GovernanceIncident.category == "memory",
                )
            )
        ).all()
        event = await db.scalar(
            select(AgentEvent).where(
                AgentEvent.run_id == second["run_id"],
                AgentEvent.event_type == "memory_retrieved",
            )
        )
        # The memory written by the supervisor carries its attestation, so it is trusted.
        assert incidents == []
        assert event is not None
        assert event.payload["accepted"] == 1
        assert event.payload["rejected"] == 0


@pytest.mark.asyncio
async def test_retrieval_quarantines_memory_without_attestation(client):
    user_id = _unique_user()
    session_id = await _session(client, user_id)
    # A preference written outside the attested supervisor path has no MemoryProvenance row.
    orphan = await app.state.supervisor.memory.remember_preference(
        user_id=user_id,
        session_id=session_id,
        content=PREFERENCE_MESSAGE,
        source="user_explicit_statement",
        source_ref="omnicare://sessions/legacy/runs/legacy",
        created_by="omnicare",
    )

    payload = await _chat(client, user_id, session_id, "我的订单物流到哪了？")
    assert payload["status"] == "completed"
    async with SessionLocal() as db:
        incident = await db.scalar(
            select(GovernanceIncident).where(
                GovernanceIncident.run_id == payload["run_id"],
                GovernanceIncident.category == "memory",
            )
        )
        event = await db.scalar(
            select(AgentEvent).where(
                AgentEvent.run_id == payload["run_id"],
                AgentEvent.event_type == "memory_retrieved",
            )
        )
        assert incident is not None
        assert incident.decision == "quarantine"
        assert incident.reason_code == "memory_provenance_unverified"
        assert orphan.id in incident.detail
        assert event is not None
        assert event.payload["rejected"] >= 1
        assert event.payload["accepted"] == 0


class _NanoMemoryRecorder:
    def __init__(
        self, *, created: dict, recalled: dict | None = None, listed: dict | None = None
    ) -> None:
        self.created = created
        self.recalled = recalled or {}
        self.listed = listed or {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json=self.created)
        if request.url.path == "/api/v1/memory/recall":
            return httpx.Response(200, json=self.recalled)
        if request.url.path == "/api/v1/memory/long-term":
            return httpx.Response(200, json=self.listed)
        return httpx.Response(404, json={"detail": "not found"})

    def adapter(self) -> NanoMemoryHTTPAdapter:
        settings = Settings(nanomemory_base_url="http://nanomemory.test")
        return NanoMemoryHTTPAdapter(settings, transport=httpx.MockTransport(self.handler))


@pytest.mark.asyncio
async def test_nanomemory_adapter_posts_provenance_and_maps_response():
    source_ref = "omnicare://sessions/s1/runs/r1"
    recorder = _NanoMemoryRecorder(
        created={
            "id": "mem-1",
            "content": PREFERENCE_MESSAGE,
            "memory_type": "preference",
            "status": "active",
            "source": "omnicare",
            "source_type": "user",
            "trust_level": "unknown",
            "provenance": {
                "source_kind": "user",
                "source_ref": source_ref,
                "created_by": "omnicare",
                "derived_from": [],
            },
        }
    )
    record = await recorder.adapter().remember_preference(
        user_id="u1",
        session_id="s1",
        content=PREFERENCE_MESSAGE,
        source="user_explicit_statement",
        source_ref=source_ref,
        created_by="omnicare",
    )
    post = next(request for request in recorder.requests if request.method == "POST")
    body = json.loads(post.content)
    assert body["provenance"] == {
        "source_kind": "user",
        "source_ref": source_ref,
        "created_by": "omnicare",
    }
    assert record.provenance == {
        "source_kind": "user",
        "source_ref": source_ref,
        "created_by": "omnicare",
    }
    assert record.trust_level == "user_stated"
    assert record.id == "mem-1"


@pytest.mark.asyncio
async def test_nanomemory_adapter_tolerates_old_server_without_provenance():
    recorder = _NanoMemoryRecorder(
        created={
            "id": "mem-legacy",
            "content": PREFERENCE_MESSAGE,
            "memory_type": "preference",
            "status": "active",
            "source": "omnicare",
            "source_type": "user",
        }
    )
    record = await recorder.adapter().remember_preference(
        user_id="u1",
        session_id="s1",
        content=PREFERENCE_MESSAGE,
        source="user_explicit_statement",
        source_ref="omnicare://sessions/s1/runs/r1",
    )
    assert record.provenance == {}


@pytest.mark.asyncio
async def test_nanomemory_adapter_maps_provenance_on_retrieve():
    source_ref = "omnicare://sessions/s1/runs/r1"
    recorder = _NanoMemoryRecorder(
        created={},
        recalled={
            "items": [
                {
                    "id": "mem-1",
                    "kind": "long_term",
                    "content": PREFERENCE_MESSAGE,
                    "memory_type": "preference",
                    "status": "active",
                }
            ]
        },
        listed={
            "records": [
                {
                    "id": "mem-1",
                    "content": PREFERENCE_MESSAGE,
                    "memory_type": "preference",
                    "status": "active",
                    "source": "omnicare",
                    "source_type": "user",
                    "provenance": {
                        "source_kind": "user",
                        "source_ref": source_ref,
                        "created_by": "omnicare",
                        "derived_from": [],
                    },
                }
            ]
        },
    )
    records = await recorder.adapter().retrieve(user_id="u1", session_id="s1", query="preference")
    assert len(records) == 1
    assert records[0].provenance["source_ref"] == source_ref
    assert records[0].provenance["created_by"] == "omnicare"
    assert records[0].trust_level == "user_stated"
