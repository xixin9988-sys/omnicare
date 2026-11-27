"""Forward answer feedback to mem-guard, which owns the trace and its badcase export.

The browser never sees a gateway key: this service adds the service credential and the
authenticated scope. A 404 from the gateway means "no such trace for this tenant" and is passed
through unchanged so the UI can distinguish "rating lost" from "rating accepted".
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.security import Principal
from app.schemas.feedback import FeedbackIn


class FeedbackError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 503,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after


async def submit_feedback(
    settings: Settings,
    principal: Principal,
    payload: FeedbackIn,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    headers = {
        "X-Guard-Tenant": principal.tenant_id,
        "X-Guard-User": principal.user_id,
    }
    if settings.memguard_service_key:
        headers["Authorization"] = f"Bearer {settings.memguard_service_key}"
    body = {
        "trace_id": payload.trace_id,
        "verdict": payload.verdict,
        "reason": payload.reason,
        "comment": payload.comment,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.memguard_base_url.rstrip("/"),
            timeout=settings.memguard_timeout_seconds,
            transport=transport,
        ) as client:
            response = await client.post("/v1/guard/feedback", headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise FeedbackError(
            "memory_gateway_unavailable",
            "The memory gateway is unreachable; the rating was not recorded.",
        ) from exc

    if response.status_code == 404:
        raise FeedbackError(
            "trace_not_found",
            "This answer has no trace for your account; the rating was not recorded.",
            status=404,
        )
    if response.status_code == 429:
        raise FeedbackError(
            "rate_limited",
            "Too many feedback submissions; retry shortly.",
            status=429,
            retry_after=response.headers.get("Retry-After"),
        )
    if response.status_code >= 400:
        raise FeedbackError(
            "feedback_rejected",
            "The memory gateway rejected the rating.",
            status=502,
        )
    return response.json()


__all__ = ["FeedbackError", "submit_feedback"]
