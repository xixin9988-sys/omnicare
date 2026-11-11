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
    (Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "nanomemory_contract.json").read_text(
        encoding="utf-8-sig"
    )
)


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/api/v1/memory/recall" and method == "GET":
            return httpx.Response(200, json=FIXTURE["recall_response"])
        if path == "/api/v1/memory/long-term" and method == "GET":
            return httpx.Response(200, json=FIXTURE["list_response"])
        if path == "/api/v1/memory/long-term" and method == "POST":
            return httpx.Response(200, json=FIXTURE["create_response"])
        return httpx.Response(404, json={"detail": "not found"})


def _adapter(recorder: _Recorder) -> NanoMemoryHTTPAdapter:
    settings = Settings(nanomemory_base_url="http://nanomemory.test")
    return NanoMemoryHTTPAdapter(settings, transport=httpx.MockTransport(recorder.handler))


@pytest.mark.asyncio
async def test_retrieve_maps_only_long_term_items_and_trust() -> None:
    recorder = _Recorder()
    adapter = _adapter(recorder)
    records = await adapter.retrieve(user_id="u1", session_id="s1", query="what does the user prefer?")

    assert records, "recall should yield long-term items"
    assert all(record.id.startswith("mem-") for record in records)
    item = records[0]
    assert item.trust_level == "user_stated", "source_type=user is the adapter attestation"
    assert item.state == "ACTIVE"
    assert item.memory_type in {"fact", "preference"}
    assert {r.url.path for r in recorder.requests} == {
        "/api/v1/memory/recall",
        "/api/v1/memory/long-term",
    }


@pytest.mark.asyncio
async def test_remember_preference_posts_real_create_payload() -> None:
    recorder = _Recorder()
    adapter = _adapter(recorder)
    record = await adapter.remember_preference(
        user_id="u1", session_id="s1", content="User prefers dark mode.", source="omnicare"
    )

    assert record.trust_level == "user_stated"
    create = [r for r in recorder.requests if r.method == "POST"][0]
    body = json.loads(create.content)
    assert body["content"] == "User prefers dark mode."
    assert body["category"] == "preference"
    assert body["source_type"] == "user"
    assert "omnicare" in body["tags"]


@pytest.mark.asyncio
async def test_recall_failure_surfaces_as_exception() -> None:
    settings = Settings(nanomemory_base_url="http://nanomemory.test")
    adapter = NanoMemoryHTTPAdapter(
        settings, transport=httpx.MockTransport(lambda request: httpx.Response(500, json={}))
    )
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.retrieve(user_id="u1", session_id="s1", query="anything")
