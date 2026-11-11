import pytest


@pytest.mark.asyncio
async def test_create_session_and_logistics_chat(client):
    created = await client.post("/api/v1/sessions", json={"user_id": "demo_user_001"})
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    response = await client.post(
        "/api/v1/chat",
        json={
            "user_id": "demo_user_001",
            "session_id": session_id,
            "message": "我的订单 ORD-2026-0001 物流到哪了？",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["agent"] == "logistics_agent"
    assert "SF1234567890" in payload["reply"]


@pytest.mark.asyncio
async def test_unknown_session_is_rejected(client):
    response = await client.post(
        "/api/v1/chat",
        json={
            "user_id": "demo_user_001",
            "session_id": "missing-session",
            "message": "查询物流",
        },
    )
    assert response.status_code == 404
