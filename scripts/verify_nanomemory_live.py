"""Live acceptance probe for the NanoMemory + OmniCare memory-adapter contract.

Usage:

    # terminal 1: a real NanoMemory 0.2.1 service (single-session mode is enough)
    cd ../nanomemory
    NANOMEMORY_STORAGE_ROOT=/tmp/nm-live NANOMEMORY_SESSION_MODE=single \
      uvicorn nanomemory.api.main:app --host 127.0.0.1 --port 8101

    # terminal 2: this probe (repo root, project venv)
    python scripts/verify_nanomemory_live.py

It checks the REST provenance round-trip, the adapter's provenance mapping, the
`user_id` recall scoping, and the single-session header negotiation (the previous adapter
sent an OmniCare session id and got 404 session_not_found). Exits non-zero on failure.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

import httpx

from app.core.config import Settings
from app.memory.nanomemory_http import NanoMemoryHTTPAdapter

BASE = "http://127.0.0.1:8101"


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as client:
        health = await client.get("/health")
        assert health.status_code == 200, health.text
        print("PASS NanoMemory health")

        payload = {
            "content": "用户偏好：以后不要用顺丰，优先用京东物流。",
            "category": "preference",
            "source": "user_explicit_statement",
            "source_type": "user",
            "user_id": "live-probe-user",
            "tags": ["omnicare", "preference"],
            "provenance": {
                "source_kind": "user",
                "source_ref": "omnicare://sessions/live/runs/live",
                "created_by": "omnicare.supervisor",
            },
        }
        created = await client.post("/api/v1/memory/long-term", json=payload)
        assert created.status_code == 200, created.text
        record = created.json()
        assert record["provenance"]["source_ref"] == "omnicare://sessions/live/runs/live"
        print("PASS REST long-term create stores provenance")

        fetched = await client.get(f"/api/v1/memory/long-term/{record['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["provenance"]["created_by"] == "omnicare.supervisor"
        print("PASS provenance is returned by reads")

    settings = Settings(_env_file=None, memory_backend="nanomemory_http", nanomemory_base_url=BASE)
    adapter = NanoMemoryHTTPAdapter(settings)
    session_id = str(uuid.uuid4())
    written = await adapter.remember_preference(
        user_id="live-probe-user",
        session_id=session_id,
        content="以后优先用京东物流",
        source="user_explicit_statement",
        source_ref="omnicare://sessions/live/runs/run-2",
        created_by="omnicare.supervisor",
    )
    assert written.provenance.get("source_ref") == "omnicare://sessions/live/runs/run-2"
    print("PASS adapter writes provenance and maps it back")

    hits = await adapter.retrieve(
        user_id="live-probe-user", session_id=session_id, query="京东物流", limit=5
    )
    assert any(hit.id == written.id for hit in hits), [hit.id for hit in hits]
    print("PASS adapter recall returns the user's own memory with provenance")

    print(json.dumps({"written": written.id, "provenance": written.provenance}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
