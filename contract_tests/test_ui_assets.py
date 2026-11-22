"""Static contract for the Synthelix-styled OmniCare web console.

The console is deliberately dependency-free: no Three.js, no canvas, no build step, no
external fonts. It is a white, Apple-like event board fed by the existing WebSocket stream and
a clearly labelled demo fallback when the socket is unavailable.

No FastAPI/SQLAlchemy import here: these checks stay runnable in a minimal environment.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"


def test_console_files_exist_and_three_js_is_gone() -> None:
    assert (WEB / "index.html").is_file()
    assert (WEB / "css" / "style.css").is_file()
    assert (WEB / "js" / "app.js").is_file()
    assert not (WEB / "vendor").exists(), "Three.js must not be vendored any more"


def test_console_is_minimal_white_and_branded() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "css" / "style.css").read_text(encoding="utf-8")
    js = (WEB / "js" / "app.js").read_text(encoding="utf-8")

    assert "Synthelix" in html, "the studio wordmark stays on the page"
    assert "#6a00ff" in css, "Synthelix gradient color is part of the palette"
    assert "#654321" in css, "Synthelix brown accent is part of the palette"
    assert "--bg: #ffffff" in css, "the canvas is white"
    assert "font-family: var(--font)" in css

    combined = html + css + js
    assert "three.module.js" not in combined
    assert "import * as THREE" not in combined
    assert "OrbitControls" not in combined
    assert "<canvas" not in html.lower()
    assert "requestAnimationFrame" not in js
    assert "importmap" not in html


def test_console_streams_events_with_a_demo_fallback() -> None:
    js = (WEB / "js" / "app.js").read_text(encoding="utf-8")
    assert "/api/v1/ws/monitor" in js
    assert "startDemo" in js
    assert "document.createElement" in js and ".prepend(" in js
    # The page only renders whitelisted event fields, never raw event HTML.
    assert "innerHTML" not in js
    assert "textContent" in js


def test_app_mounts_ui_and_redirects_root() -> None:
    main = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert re.search(r'mount\(\s*"/ui"', main)
    assert 'RedirectResponse(url="/ui/")' in main
