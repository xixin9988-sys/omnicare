"""Live acceptance probe for the OmniCare MCP server (streamable HTTP + JWT).

Usage (from the repo root, with the project venv):

    export APP_ENV=local AUTH_MODE=jwt AUTH_SECRET=<32+ chars>
    export AUTH_JWT_ISSUER=http://127.0.0.1:8000 AUTH_JWT_AUDIENCE=omnicare-api
    export MCP_REQUIRED_SCOPES=omnicare.read MCP_RESOURCE_URL=http://127.0.0.1:9000/mcp
    python -m app.mcp_server            # terminal 1 (or a background process)
    python scripts/verify_mcp_live.py   # terminal 2

Issues its own HS256 JWTs with app.core.jwt (so it needs AUTH_SECRET). Requires the demo
Database to be seeded (the API seeds it; or run app.main.seed_demo_data once). Prints
PASS/FAIL lines; exits non-zero on failure. No token is ever printed.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

from app.core.config import Settings
from app.core.jwt import issue_jwt

BASE = os.environ.get("MCP_BASE_URL", "http://127.0.0.1:9000/mcp")
PROTOCOL = "2025-06-18"
SETTINGS = Settings()

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(("PASS " if condition else "FAIL ") + name + (f" :: {detail}" if detail else ""))


def parse_response(response: httpx.Response) -> list[dict]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        events = []
        for block in response.text.split("\n\n"):
            data = "\n".join(
                line[5:].strip() for line in block.splitlines() if line.startswith("data:")
            )
            if data:
                events.append(json.loads(data))
        return events
    if not response.text:
        return []
    return [response.json()]


def initialize(client: httpx.Client, token: str | None) -> tuple[int, httpx.Response]:
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = client.post(
        BASE,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "omnicare-live-probe", "version": "0"},
            },
        },
        headers=headers,
    )
    return response.status_code, response


def session_headers(token: str, session_id: str | None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Mcp-Protocol-Version": PROTOCOL,
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def rpc(client: httpx.Client, token: str, session_id: str, payload: dict) -> list[dict]:
    return parse_response(
        client.post(BASE, json=payload, headers=session_headers(token, session_id))
    )


def tool_payload(events: list[dict]) -> dict:
    """Extract a tool's JSON result (structured output or text content)."""
    if not events:
        return {}
    result = events[0].get("result") or {}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        inner = structured.get("result", structured)
        if isinstance(inner, dict):
            return inner
    for block in result.get("content") or []:
        if block.get("type") == "text":
            try:
                parsed = json.loads(block.get("text") or "")
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def main() -> int:
    alice = issue_jwt(
        "demo_user_001",
        settings=SETTINGS,
        role="user",
        scopes=("omnicare.read",),
    )
    bob = issue_jwt("bob", settings=SETTINGS, role="user", scopes=("omnicare.read",))
    expired = issue_jwt(
        "demo_user_001",
        settings=SETTINGS,
        scopes=("omnicare.read",),
        ttl_seconds=10,
        now=0,
    )
    scarce = issue_jwt("demo_user_001", settings=SETTINGS, scopes=("other.scope",))

    with httpx.Client(timeout=20.0) as client:
        status, response = initialize(client, None)
        check("no bearer token is refused with 401", status == 401, f"status={status}")

        status, response = initialize(client, "not-a-jwt")
        check("garbage bearer token is refused with 401", status == 401, f"status={status}")

        status, response = initialize(client, expired)
        check("expired token is refused with 401", status == 401, f"status={status}")

        status, response = initialize(client, scarce)
        check(
            "token without required scope is refused (401/403)",
            status in {401, 403},
            f"status={status}",
        )

        status, response = initialize(client, alice)
        check("valid JWT initializes a session", status == 200, f"status={status}")
        session_id = response.headers.get("mcp-session-id")
        check("server issues a session id", bool(session_id))

        initialized = client.post(
            BASE,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=session_headers(alice, session_id),
        )
        check("initialized notification accepted", initialized.status_code == 202)

        listed = rpc(
            client,
            alice,
            session_id,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        tools = {tool["name"]: tool for tool in listed[0]["result"]["tools"]} if listed else {}
        check(
            "tools/list returns the two read-only tools",
            set(tools) == {"search_products", "check_logistics"},
        )
        for name, tool in tools.items():
            annotations = tool.get("annotations") or {}
            check(
                f"{name} publishes read-only annotations",
                annotations.get("readOnlyHint") is True
                and annotations.get("destructiveHint") is False,
            )
        logistics_schema = (tools.get("check_logistics") or {}).get("inputSchema", {})
        props = set((logistics_schema.get("properties") or {}).keys())
        check(
            "check_logistics accepts no token/user_id arguments", props == {"order_id"}, str(props)
        )

        searched = rpc(
            client,
            alice,
            session_id,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "search_products", "arguments": {"query": "跑鞋"}},
            },
        )
        check("search_products executes over the authenticated transport", bool(searched))

        own = rpc(
            client,
            alice,
            session_id,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "check_logistics", "arguments": {"order_id": "ORD-2026-0001"}},
            },
        )
        own_payload = tool_payload(own)
        check(
            "alice reads her own order through the token identity",
            bool(own_payload) and own_payload.get("ok") is True,
            json.dumps(own_payload, ensure_ascii=False)[:160],
        )

        status, response = initialize(client, bob)
        bob_session = response.headers.get("mcp-session-id")
        client.post(
            BASE,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=session_headers(bob, bob_session),
        )
        other = rpc(
            client,
            bob,
            bob_session,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "check_logistics", "arguments": {"order_id": "ORD-2026-0001"}},
            },
        )
        other_payload = tool_payload(other)
        check(
            "bob cannot read alice's order (identity comes from the token)",
            bool(other_payload) and other_payload.get("ok") is False,
            json.dumps(other_payload, ensure_ascii=False)[:160],
        )

        body_text = " ".join(
            [
                response.text,
                json.dumps(listed, ensure_ascii=False),
                json.dumps(own, ensure_ascii=False),
            ]
        )
        check(
            "raw token never appears in responses", alice not in body_text and bob not in body_text
        )

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
