"""Feedback endpoint: auth, forwarding and error mapping."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.api import routes
from app.services.feedback import FeedbackError

DEV_HEADERS = {"X-User-Id": "demo_user_001"}


@pytest.mark.asyncio
async def test_feedback_forwards_to_gateway(client: AsyncClient, monkeypatch) -> None:
    captured: dict = {}

    async def fake_submit(settings, principal, payload):
        captured["user"] = principal.user_id
        captured["trace_id"] = payload.trace_id
        return {
            "feedback_id": "fb-1",
            "trace_id": payload.trace_id,
            "verdict": payload.verdict,
            "forwarded": True,
            "created_at": "2026-11-27T10:00:00+00:00",
        }

    monkeypatch.setattr(routes, "submit_feedback", fake_submit)
    response = await client.post(
        "/api/v1/feedback",
        headers=DEV_HEADERS,
        json={"trace_id": "tr-abc", "verdict": "down", "reason": "wrong_memory"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["feedback_id"] == "fb-1"
    assert body["trace_id"] == "tr-abc"
    assert captured["user"] == "demo_user_001"


@pytest.mark.asyncio
async def test_feedback_rejects_an_invalid_token(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/feedback",
        headers={"Authorization": "Bearer not-a-valid-token"},
        json={"trace_id": "tr-abc", "verdict": "up"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_unknown_trace_is_404(client: AsyncClient, monkeypatch) -> None:
    async def fake_submit(settings, principal, payload):
        raise FeedbackError("trace_not_found", "No such trace.", status=404)

    monkeypatch.setattr(routes, "submit_feedback", fake_submit)
    response = await client.post(
        "/api/v1/feedback",
        headers=DEV_HEADERS,
        json={"trace_id": "tr-missing", "verdict": "up"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "trace_not_found"


@pytest.mark.asyncio
async def test_rate_limited_feedback_carries_retry_after(client: AsyncClient, monkeypatch) -> None:
    async def fake_submit(settings, principal, payload):
        raise FeedbackError("rate_limited", "slow down", status=429, retry_after="30")

    monkeypatch.setattr(routes, "submit_feedback", fake_submit)
    response = await client.post(
        "/api/v1/feedback",
        headers=DEV_HEADERS,
        json={"trace_id": "tr-abc", "verdict": "down"},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"


@pytest.mark.asyncio
async def test_gateway_outage_is_503(client: AsyncClient, monkeypatch) -> None:
    async def fake_submit(settings, principal, payload):
        raise FeedbackError("memory_gateway_unavailable", "down", status=503)

    monkeypatch.setattr(routes, "submit_feedback", fake_submit)
    response = await client.post(
        "/api/v1/feedback",
        headers=DEV_HEADERS,
        json={"trace_id": "tr-abc", "verdict": "up"},
    )
    assert response.status_code == 503
