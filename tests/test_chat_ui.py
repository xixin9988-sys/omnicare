"""Chat surface: assets, explicit states and the feedback wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

WEB = Path(__file__).resolve().parents[1] / "web"


def test_chat_page_references_its_assets() -> None:
    html = (WEB / "chat.html").read_text(encoding="utf-8")
    assert "/ui/js/chat.js" in html
    assert "/ui/css/chat.css" in html
    assert "chat-banner" in html
    assert "chat-feedback" not in html  # rendered per answer, not statically


def test_chat_script_covers_every_state_and_the_feedback_loop() -> None:
    script = (WEB / "js" / "chat.js").read_text(encoding="utf-8")
    for token in (
        "/api/v1/sessions",
        "/api/v1/chat",
        "/api/v1/feedback",
        "status === 401",
        "status === 403",
        "status === 429",
        "status >= 500",
        "Retry-After",
        "trace_id",
        "memory_degraded",
    ):
        assert token in script, f"missing {token}"


def test_chat_css_has_loading_and_feedback_states() -> None:
    css = (WEB / "css" / "chat.css").read_text(encoding="utf-8")
    for token in ("chat-typing", "chat-feedback", "chat-reasons", "chat-empty", "chat-banner"):
        assert token in css


@pytest.mark.asyncio
async def test_chat_page_is_served(client: AsyncClient) -> None:
    page = await client.get("/ui/chat.html")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    script = await client.get("/ui/js/chat.js")
    assert script.status_code == 200
    assert "requestReply" in script.text
