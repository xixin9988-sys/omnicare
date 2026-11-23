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


DEMO_FILES = ("demo.html", "css/demo.css", "js/demo.js")


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_demo_page_exists_and_is_dependency_free() -> None:
    for name in DEMO_FILES:
        assert (WEB / name).is_file(), f"{name} must ship with the 60s demo page"

    html = _read("demo.html")
    css = _read("css/demo.css")
    js = _read("js/demo.js")

    assert "./css/demo.css" in html, "the demo page has its own stylesheet"
    assert "./js/demo.js" in html, "the demo page has its own script"
    assert not re.search(r'(?:src|href)="(?:https?:)?//', html), "no external resources"

    combined = html + css + js
    for banned in (
        "three.module.js",
        "OrbitControls",
        "cdn.",
        "unpkg",
        "jsdelivr",
        "fonts.googleapis",
        "requestAnimationFrame",
    ):
        assert banned not in combined
    assert "@import" not in css
    # Server payloads are inserted as text nodes, never as markup.
    assert "innerHTML" not in js
    assert "textContent" in js


def test_demo_page_drives_the_real_api_contract() -> None:
    js = _read("js/demo.js")
    assert "/api/v1" in js
    assert "/demo/manifest" in js
    assert "/demo/runs" in js
    assert "/steps/" in js
    assert "/replay" in js
    assert "autoplay" in js
    assert "embed" in js
    # A disabled backend is reported as such; the page never fakes a local run.
    assert "demo_disabled" in js


def test_console_links_to_the_demo_page() -> None:
    html = _read("index.html")
    assert "demo.html" in html, "the console must link to the 60s demo page"
