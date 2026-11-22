"""Contract tests for the NanoMemory HTTP adapter.

Fixtures were captured from a real NanoMemory 0.2.0 TestClient run (see
``tests/fixtures/nanomemory_contract.json``); the transport is mocked so the test is
deterministic and needs no server. Run:

    python -m pytest contract_tests -q
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.memory.nanomemory_http import NanoMemoryHTTPAdapter

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "nanomemory_contract.json"
    ).read_text(encoding="utf-8-sig")
)


class _Recorder:
    def __init__(
        self,
        *,
        session_mode: str = "single",
        created_session_id: str = "nm-session-0001",
        create_response: dict | None = None,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.session_mode = session_mode
        self.created_session_id = created_session_id
        self.create_response = create_response or FIXTURE["create_response"]
        self.session_creations = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/api/v1/version" and method == "GET":
            return httpx.Response(200, json={"session_mode": self.session_mode})
        if path == "/api/v1/sessions" and method == "POST":
            self.session_creations += 1
            return httpx.Response(
                201,
                json={
                    "session_id": self.created_session_id,
                    "created_at": "2026-11-11T07:10:59+00:00",
                    "status": "active",
                },
            )
        if path == "/api/v1/memory/recall" and method == "GET":
            return httpx.Response(200, json=FIXTURE["recall_response"])
        if path == "/api/v1/memory/long-term" and method == "GET":
            return httpx.Response(200, json=FIXTURE["list_response"])
        if path == "/api/v1/memory/long-term" and method == "POST":
            return httpx.Response(200, json=self.create_response)
        return httpx.Response(404, json={"detail": "not found"})


def _adapter(recorder: _Recorder) -> NanoMemoryHTTPAdapter:
    settings = Settings(_env_file=None, nanomemory_base_url="http://nanomemory.test")
    return NanoMemoryHTTPAdapter(settings, transport=httpx.MockTransport(recorder.handler))


@pytest.mark.asyncio
async def test_retrieve_maps_only_long_term_items_and_trust() -> None:
    recorder = _Recorder()
    adapter = _adapter(recorder)
    records = await adapter.retrieve(
        user_id="u1", session_id="s1", query="what does the user prefer?"
    )

    assert records, "recall should yield long-term items"
    assert all(record.id.startswith("mem-") for record in records)
    item = records[0]
    assert item.trust_level == "user_stated", "source_type=user is the adapter attestation"
    assert item.state == "ACTIVE"
    assert item.memory_type in {"fact", "preference"}
    assert {r.url.path for r in recorder.requests} == {
        "/api/v1/version",
        "/api/v1/memory/recall",
        "/api/v1/memory/long-term",
    }


@pytest.mark.asyncio
async def test_single_session_deployment_omits_the_session_header() -> None:
    recorder = _Recorder(session_mode="single")
    adapter = _adapter(recorder)
    await adapter.remember_preference(
        user_id="u1", session_id="s1", content="User prefers dark mode.", source="omnicare"
    )
    await adapter.retrieve(user_id="u1", session_id="s1", query="prefer")

    memory_requests = [r for r in recorder.requests if "/memory/" in r.url.path]
    assert memory_requests
    assert all("x-nanomemory-session-id" not in r.headers for r in memory_requests)
    assert recorder.session_creations == 0
    # The OmniCare session id is not a NanoMemory session id; in single-session mode the
    # server uses its default session and rejects unknown ids, so omitting is correct.


@pytest.mark.asyncio
async def test_multi_session_deployment_creates_and_reuses_a_nanomemory_session() -> None:
    recorder = _Recorder(session_mode="multi", created_session_id="nm-session-xyz")
    adapter = _adapter(recorder)
    await adapter.remember_preference(
        user_id="u1",
        session_id="omnicare-session-1",
        content="User prefers tea.",
        source="omnicare",
    )
    await adapter.retrieve(user_id="u1", session_id="omnicare-session-1", query="tea")
    await adapter.remember_preference(
        user_id="u1",
        session_id="omnicare-session-1",
        content="User prefers oat milk.",
        source="omnicare",
    )

    assert recorder.session_creations == 1, "one NanoMemory session per OmniCare conversation"
    memory_requests = [r for r in recorder.requests if "/memory/" in r.url.path]
    assert memory_requests
    assert all(
        r.headers.get("x-nanomemory-session-id") == "nm-session-xyz" for r in memory_requests
    )


@pytest.mark.asyncio
async def test_multi_session_deployment_uses_a_distinct_session_per_conversation() -> None:
    recorder = _Recorder(session_mode="multi", created_session_id="nm-session-1")
    adapter = _adapter(recorder)
    await adapter.remember_preference(
        user_id="u1", session_id="conversation-a", content="User prefers tea.", source="omnicare"
    )
    recorder.created_session_id = "nm-session-2" if hasattr(recorder, "created_session_id") else ""
    await adapter.remember_preference(
        user_id="u1", session_id="conversation-b", content="User prefers coffee.", source="omnicare"
    )
    assert recorder.session_creations == 2


@pytest.mark.asyncio
async def test_remember_preference_posts_real_create_payload() -> None:
    recorder = _Recorder()
    adapter = _adapter(recorder)
    record = await adapter.remember_preference(
        user_id="u1", session_id="s1", content="User prefers dark mode.", source="omnicare"
    )

    assert record.trust_level == "user_stated"
    create = next(r for r in recorder.requests if r.method == "POST")
    body = json.loads(create.content)
    assert body["content"] == "User prefers dark mode."
    assert body["category"] == "preference"
    assert body["source_type"] == "user"
    assert body["user_id"] == "u1", "recall?user_id= only finds records written with the owner"
    assert "omnicare" in body["tags"]
    assert "provenance" not in body, "no source_ref means no provenance claim"


@pytest.mark.asyncio
async def test_remember_preference_forwards_provenance_source_ref() -> None:
    recorder = _Recorder()
    adapter = _adapter(recorder)
    record = await adapter.remember_preference(
        user_id="u1",
        session_id="s1",
        content="User prefers dark mode.",
        source="user_explicit_statement",
        source_ref="omnicare://sessions/s1/runs/r1",
        created_by="omnicare.supervisor",
    )

    create = next(r for r in recorder.requests if r.method == "POST")
    body = json.loads(create.content)
    assert body["provenance"] == {
        "source_kind": "user",
        "source_ref": "omnicare://sessions/s1/runs/r1",
        "created_by": "omnicare.supervisor",
    }
    # The fixture response has no usable source_ref, so the record has no ref yet.
    assert record.provenance.get("source_ref", "") in {"", "omnicare://sessions/s1/runs/r1"}


@pytest.mark.asyncio
async def test_create_response_provenance_is_mapped_onto_the_record() -> None:
    create_response = dict(FIXTURE["create_response"])
    create_response["provenance"] = {
        "source_kind": "user",
        "source_ref": "omnicare://sessions/s1/runs/r9",
        "created_by": "omnicare.supervisor",
        "derived_from": [],
        "note": None,
    }
    recorder = _Recorder(create_response=create_response)
    adapter = _adapter(recorder)
    record = await adapter.remember_preference(
        user_id="u1",
        session_id="s1",
        content="User prefers dark mode.",
        source="user_explicit_statement",
        source_ref="omnicare://sessions/s1/runs/r9",
    )
    assert record.provenance.get("source_ref") == "omnicare://sessions/s1/runs/r9"
    assert record.provenance.get("source_kind") == "user"


@pytest.mark.asyncio
async def test_recall_failure_surfaces_as_exception() -> None:
    settings = Settings(_env_file=None, nanomemory_base_url="http://nanomemory.test")
    adapter = NanoMemoryHTTPAdapter(
        settings, transport=httpx.MockTransport(lambda request: httpx.Response(500, json={}))
    )
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.retrieve(user_id="u1", session_id="s1", query="anything")
