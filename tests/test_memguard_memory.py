"""mem-guard memory adapter: tenant headers, mapping, degradation."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.memory.memguard_http import MemGuardMemoryAdapter
from app.memory.ports import MemoryBackendError


def settings(**overrides) -> Settings:
    base = {"_env_file": None, "app_env": "test", "memory_backend": "memguard_http"}
    base.update(overrides)
    return Settings(**base)


def test_retrieve_maps_items_and_records_trace_id() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "trace_id": "tr-123",
                "items": [
                    {
                        "id": "vec-1",
                        "content": "用户偏好深色模式",
                        "score": 0.8,
                        "metadata": {
                            "memory_type": "preference",
                            "source": "user_turn",
                            "trust_level": "user_provided",
                            "provenance": {"source_kind": "user", "source_ref": "s1"},
                        },
                    }
                ],
                "insufficient_context": False,
                "guard": {"decision": "allow"},
            },
        )

    adapter = MemGuardMemoryAdapter(
        settings(memguard_service_key="svc", default_tenant_id="default"),
        transport=httpx.MockTransport(handler),
    )

    async def run():
        return await adapter.retrieve(
            user_id="u-1", session_id="s-1", query="偏好", tenant_id="tenant-a"
        )

    records = asyncio_run(run())
    assert records[0].id == "vec-1"
    assert records[0].trust_level == "user_provided"
    assert records[0].provenance["source_ref"] == "s1"
    assert adapter.last_trace_id == "tr-123"
    request = captured[0]
    assert request.headers["X-Guard-Tenant"] == "tenant-a"
    assert request.headers["X-Guard-User"] == "u-1"
    assert request.headers["Authorization"] == "Bearer svc"


def test_insufficient_context_yields_no_memories() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"trace_id": "tr-2", "items": [], "insufficient_context": True},
        )

    adapter = MemGuardMemoryAdapter(settings(), transport=httpx.MockTransport(handler))

    async def run():
        return await adapter.retrieve(user_id="u", session_id="s", query="x")

    assert asyncio_run(run()) == []


def test_upstream_error_is_typed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": "quota_exceeded", "message": "budget"})

    adapter = MemGuardMemoryAdapter(settings(), transport=httpx.MockTransport(handler))

    async def run():
        return await adapter.retrieve(user_id="u", session_id="s", query="x")

    with pytest.raises(MemoryBackendError) as excinfo:
        asyncio_run(run())
    assert excinfo.value.code == "quota_exceeded"
    assert excinfo.value.status == 429


def test_remember_preference_uses_the_guard_path() -> None:
    captured: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {"path": request.url.path, "body": __import__("json").loads(request.content)}
        )
        return httpx.Response(
            200,
            json={
                "record": {
                    "id": "vec-2",
                    "metadata": {"memory_type": "preference", "trust_level": "user_provided"},
                },
                "trace_id": "tr-3",
            },
        )

    adapter = MemGuardMemoryAdapter(settings(), transport=httpx.MockTransport(handler))

    async def run():
        return await adapter.remember_preference(
            user_id="u",
            session_id="s",
            content="以后优先使用顺丰",
            source="user_explicit_statement",
            source_ref="omnicare://s/r/1",
        )

    record = asyncio_run(run())
    assert record.id == "vec-2"
    assert captured[0]["path"] == "/v1/guard/remember"
    assert captured[0]["body"]["source_kind"] == "user"


def test_unreachable_gateway_is_typed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    adapter = MemGuardMemoryAdapter(settings(), transport=httpx.MockTransport(handler))

    async def run():
        return await adapter.retrieve(user_id="u", session_id="s", query="x")

    with pytest.raises(MemoryBackendError) as excinfo:
        asyncio_run(run())
    assert excinfo.value.code == "memory_gateway_unavailable"


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
