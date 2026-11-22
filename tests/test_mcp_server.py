"""Runtime tests for the authenticated MCP server (mcp 2.3, no live network).

Covers:
* the in-memory dev demo path (identity = settings.demo_user_id, tool schemas token-free),
* the fail-closed jwt path when a tool is called without transport authentication,
* the real streamable-HTTP ASGI auth matrix: 401 without / with invalid tokens, and a
  working session for a valid Bearer JWT that can only read the caller's own order.
"""

from __future__ import annotations

import json
import logging
import time
from decimal import Decimal

import httpx
import pytest
from mcp import Client

from app.core.config import Settings
from app.core.db import SessionLocal
from app.core.jwt import issue_jwt
from app.core.preflight import ConfigurationError
from app.mcp_server import create_server
from app.models.entities import Order, Product, User

JWT_SECRET = "mcp-runtime-secret-" + "x" * 32
ISSUER = "https://issuer.omnicare.test"
AUDIENCE = "omnicare-mcp"
RESOURCE_URL = "http://127.0.0.1:9000/mcp"
BASE_URL = "http://127.0.0.1:9000"
REQUIRED_SCOPE = "omnicare.read"

DEMO_USER = "demo_user_001"
DEMO_ORDER = "ORD-MCP-DEMO"
ALICE_ORDER = "ORD-MCP-ALICE"
BOB_ORDER = "ORD-MCP-BOB"

INITIALIZE_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "omnicare-pytest", "version": "0"},
    },
}


# ------------------------------------------------------------------ settings helpers
def dev_settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "app_env": "local",
        "auth_mode": "dev",
        "demo_user_id": DEMO_USER,
    }
    base.update(overrides)
    return Settings(**base)


def jwt_settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "app_env": "test",
        "auth_mode": "jwt",
        "auth_secret": JWT_SECRET,
        "auth_jwt_issuer": ISSUER,
        "auth_jwt_audience": AUDIENCE,
        "mcp_resource_url": RESOURCE_URL,
        "mcp_required_scopes": REQUIRED_SCOPE,
    }
    base.update(overrides)
    return Settings(**base)


def bearer_token(
    subject: str, *, cfg: Settings | None = None, scopes=(REQUIRED_SCOPE,), **kwargs
) -> str:
    return issue_jwt(subject, settings=cfg or jwt_settings(), scopes=list(scopes), **kwargs)


# ------------------------------------------------------------------ database seed
async def seed_database() -> None:
    async with SessionLocal() as db:
        # PostgreSQL enforces the users -> orders FK and SQLAlchemy has no relationship to
        # order these inserts, so flush the parents first (SQLite hid this locally).
        db.add_all(
            [
                User(id=DEMO_USER, display_name="演示用户"),
                User(id="alice", display_name="Alice"),
                User(id="bob", display_name="Bob"),
            ]
        )
        await db.flush()
        db.add_all(
            [
                Product(
                    id="SKU-SHOE-001",
                    sku="SHOE-001",
                    name="轻云缓震跑鞋",
                    description="日常跑步轻量缓震鞋",
                    price=Decimal("399.00"),
                    stock=42,
                ),
                Order(
                    id=DEMO_ORDER,
                    user_id=DEMO_USER,
                    status="shipped",
                    carrier="顺丰速运",
                    tracking_no="SF-DEMO-0001",
                    logistics_status="运输中",
                    total_amount=Decimal("399.00"),
                ),
                Order(
                    id=ALICE_ORDER,
                    user_id="alice",
                    status="shipped",
                    carrier="顺丰速运",
                    tracking_no="SF-ALICE-0001",
                    logistics_status="运输中",
                    total_amount=Decimal("399.00"),
                ),
                Order(
                    id=BOB_ORDER,
                    user_id="bob",
                    status="delivered",
                    carrier="京东物流",
                    tracking_no="JD-BOB-0001",
                    logistics_status="已签收",
                    total_amount=Decimal("599.00"),
                ),
            ]
        )
        await db.commit()


# ------------------------------------------------------------------ JSON-RPC helpers
def rpc(method: str, params: dict | None = None, request_id: int = 2) -> dict:
    body: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def rpc_payload(response: httpx.Response) -> dict:
    """Parse a JSON or SSE (text/event-stream) JSON-RPC response."""
    if "text/event-stream" in response.headers.get("content-type", ""):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line.removeprefix("data:").strip())
        raise AssertionError(f"no SSE data line in response: {response.text[:400]!r}")
    return response.json()


async def post_rpc(
    client: httpx.AsyncClient,
    body: dict,
    *,
    token: str | None = None,
    session_id: str | None = None,
) -> httpx.Response:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if session_id is not None:
        headers["mcp-session-id"] = session_id
    return await client.post("/mcp", json=body, headers=headers)


async def open_session(client: httpx.AsyncClient, token: str, transcript: list[str]) -> str:
    response = await post_rpc(client, INITIALIZE_BODY, token=token)
    transcript.append(response.text)
    assert response.status_code == 200, response.text
    session_id = response.headers.get("mcp-session-id")
    assert session_id, "initialize must return an mcp-session-id header"

    ack = await post_rpc(
        client,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        token=token,
        session_id=session_id,
    )
    transcript.append(ack.text)
    assert ack.status_code == 202, ack.text
    return session_id


def tool_payload(response: httpx.Response) -> object:
    result = rpc_payload(response)["result"]
    if result.get("structuredContent") is not None:
        structured = result["structuredContent"]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return json.loads(result["content"][0]["text"])


def in_memory_payload(result) -> object:
    """Return a tool's Python return value from an in-memory Client result."""
    if result.structured_content is not None:
        structured = result.structured_content
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return json.loads(result.content[0].text)


# ------------------------------------------------------------------ in-memory runtime
@pytest.mark.asyncio
async def test_in_memory_tools_use_the_demo_identity_and_token_free_schemas():
    await seed_database()
    async with Client(create_server(dev_settings())) as client:
        listed = await client.list_tools()
        tools = {tool.name: tool for tool in listed.tools}
        assert set(tools) == {"search_products", "check_logistics"}

        # (b) input schemas carry only business arguments - no token / user_id fields.
        assert set(tools["search_products"].input_schema["properties"]) == {"query"}
        assert set(tools["check_logistics"].input_schema["properties"]) == {"order_id"}
        for name, tool in tools.items():
            properties = tool.input_schema["properties"]
            assert "user_id" not in properties, name
            assert not [key for key in properties if "token" in key], name

        own = in_memory_payload(await client.call_tool("check_logistics", {"order_id": DEMO_ORDER}))
        assert own["ok"] is True
        assert own["tracking_no"] == "SF-DEMO-0001"

        other = in_memory_payload(
            await client.call_tool("check_logistics", {"order_id": ALICE_ORDER})
        )
        assert other == {"ok": False, "error": "order_not_found_or_not_owned"}

        products = in_memory_payload(await client.call_tool("search_products", {"query": "跑鞋"}))
        assert isinstance(products, list) and products
        assert products[0]["sku"] == "SHOE-001"


@pytest.mark.asyncio
async def test_jwt_mode_tools_fail_closed_without_transport_identity():
    await seed_database()
    async with Client(create_server(jwt_settings())) as client:
        result = await client.call_tool("check_logistics", {"order_id": ALICE_ORDER})
        payload = json.loads(result.content[0].text)
        assert payload == {"ok": False, "error": "mcp_identity_required"}


# ------------------------------------------------------------------ mode guards
def test_unauthenticated_modes_are_refused_outside_local_dev():
    for cfg in (
        Settings(_env_file=None, app_env="production", auth_mode="dev"),
        Settings(_env_file=None, app_env="production", auth_mode="token", auth_secret="x" * 48),
        Settings(_env_file=None, app_env="test", auth_mode="bogus"),
    ):
        with pytest.raises(ConfigurationError) as error:
            create_server(cfg)
        codes = {issue.code for issue in error.value.report.errors}
        assert codes & {"mcp_requires_jwt_auth", "unknown_auth_mode"}

    # The legacy token mode still works as a local demo.
    assert (
        create_server(
            Settings(_env_file=None, app_env="local", auth_mode="token", auth_secret="x" * 48)
        )
        is not None
    )


def test_invalid_auth_settings_urls_raise_configuration_error():
    with pytest.raises(ConfigurationError) as issuer_error:
        create_server(jwt_settings(auth_jwt_issuer="not-a-url"))
    assert {issue.code for issue in issuer_error.value.report.errors} == {"mcp_invalid_url"}

    with pytest.raises(ConfigurationError) as resource_error:
        create_server(jwt_settings(mcp_resource_url="still-not-a-url"))
    assert {issue.code for issue in resource_error.value.report.errors} == {"mcp_invalid_url"}


# ------------------------------------------------------------------ streamable HTTP auth
@pytest.mark.asyncio
async def test_streamable_http_requires_a_valid_bearer_jwt(caplog):
    caplog.set_level(logging.DEBUG)
    await seed_database()

    settings = jwt_settings()
    alice_token = bearer_token("alice", cfg=settings)
    bob_token = bearer_token("bob", cfg=settings)
    expired_token = bearer_token("alice", cfg=settings, ttl_seconds=60, now=time.time() - 3600)
    under_scoped_token = bearer_token("carol", cfg=settings, scopes=["products:read"])
    foreign_token = bearer_token("alice", cfg=jwt_settings(auth_secret="another-" + "z" * 40))

    app = create_server(settings).streamable_http_app()
    transcript: list[str] = []

    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client,
    ):
        # 1. No credential -> 401 with a Bearer challenge.
        response = await post_rpc(client, INITIALIZE_BODY)
        transcript.append(response.text)
        assert response.status_code == 401
        assert "Bearer" in response.headers.get("www-authenticate", "")

        # 2. Invalid credentials -> 401 (foreign signature, expired, under-scoped, junk).
        for bad_token in (foreign_token, expired_token, under_scoped_token, "not-a-jwt"):
            response = await post_rpc(client, INITIALIZE_BODY, token=bad_token)
            transcript.append(response.text)
            assert response.status_code == 401, (response.status_code, response.text)

        # 3. A valid JWT opens a session; tools are listed over the real transport.
        alice_session = await open_session(client, alice_token, transcript)
        response = await post_rpc(
            client, rpc("tools/list"), token=alice_token, session_id=alice_session
        )
        transcript.append(response.text)
        assert response.status_code == 200
        tools = {tool["name"]: tool for tool in rpc_payload(response)["result"]["tools"]}
        assert set(tools) == {"search_products", "check_logistics"}

        for name, expected_properties in (
            ("search_products", {"query"}),
            ("check_logistics", {"order_id"}),
        ):
            schema = tools[name]["inputSchema"]
            assert set(schema["properties"]) == expected_properties, name
            assert "user_id" not in schema["properties"], name
            assert not [key for key in schema["properties"] if "token" in key], name
        assert tools["check_logistics"]["inputSchema"]["required"] == ["order_id"]

        annotations = tools["check_logistics"]["annotations"]
        assert annotations["readOnlyHint"] is True
        assert annotations["destructiveHint"] is False
        assert annotations["idempotentHint"] is True
        assert annotations["openWorldHint"] is False

        # 4. Alice reads her own order.
        response = await post_rpc(
            client,
            rpc(
                "tools/call",
                {"name": "check_logistics", "arguments": {"order_id": ALICE_ORDER}},
            ),
            token=alice_token,
            session_id=alice_session,
        )
        transcript.append(response.text)
        assert response.status_code == 200
        assert tool_payload(response) == {
            "ok": True,
            "order_id": ALICE_ORDER,
            "carrier": "顺丰速运",
            "tracking_no": "SF-ALICE-0001",
            "status": "运输中",
        }

        # 5. Bob's valid token cannot read Alice's order (his own authenticated session).
        bob_session = await open_session(client, bob_token, transcript)
        response = await post_rpc(
            client,
            rpc(
                "tools/call",
                {"name": "check_logistics", "arguments": {"order_id": ALICE_ORDER}},
            ),
            token=bob_token,
            session_id=bob_session,
        )
        transcript.append(response.text)
        assert response.status_code == 200
        assert tool_payload(response) == {
            "ok": False,
            "error": "order_not_found_or_not_owned",
        }

        # 6. A session is bound to the credential that created it: replay as Bob -> 404.
        response = await post_rpc(
            client, rpc("tools/list"), token=bob_token, session_id=alice_session
        )
        transcript.append(response.text)
        assert response.status_code == 404

    # 7. No response body and no log line ever contains a raw token.
    transcript_text = "\n".join(transcript)
    for raw_token in (alice_token, bob_token, expired_token, under_scoped_token, foreign_token):
        assert raw_token not in transcript_text
        assert raw_token not in caplog.text
    assert "not-a-jwt" not in transcript_text
