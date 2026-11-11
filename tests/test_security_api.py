"""Authorization regressions (needs the full runtime: FastAPI + SQLAlchemy)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture()
async def client():
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as async_client,
    ):
        yield async_client


@pytest.mark.asyncio
async def test_session_rejects_spoofed_user_id(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/sessions",
        json={"user_id": "someone-else"},
        headers={"X-User-Id": "demo_user_001"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "user_id_mismatch"


@pytest.mark.asyncio
async def test_chat_rejects_spoofed_user_id(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/sessions",
        json={"user_id": "demo_user_001"},
        headers={"X-User-Id": "demo_user_001"},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    response = await client.post(
        "/api/v1/chat",
        json={"session_id": session_id, "user_id": "intruder", "message": "hi"},
        headers={"X-User-Id": "demo_user_001"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_endpoints_require_admin(client: AsyncClient) -> None:
    for path in ("/api/v1/events", "/api/v1/metrics/summary", "/api/v1/governance/incidents"):
        response = await client.get(path, headers={"X-User-Id": "demo_user_001"})
        assert response.status_code == 403, path
        assert response.json()["detail"]["code"] == "admin_required"
