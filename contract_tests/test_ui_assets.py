"""Static contract for the realtime Three.js UI.

No FastAPI/SQLAlchemy import here: these checks stay runnable in a minimal environment.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"


def test_ui_files_exist() -> None:
    assert (WEB / "index.html").is_file()
    assert (WEB / "css" / "style.css").is_file()
    assert (WEB / "js" / "app.js").is_file()


def test_index_wires_three_and_canvas() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert '<canvas id="scene"' in html
    assert '"three":' in html and "three.module.js" in html
    assert "type=\"module\"" in html and "js/app.js" in html


def test_scene_uses_websocket_and_demo_fallback() -> None:
    js = (WEB / "js" / "app.js").read_text(encoding="utf-8")
    assert "/api/v1/ws/monitor" in js
    assert "startDemo" in js
    assert "OrbitControls" in js
    assert "requestAnimationFrame" in js


def test_app_mounts_ui_and_redirects_root() -> None:
    main = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert re.search(r'mount\(\s*"/ui"', main)
    assert 'RedirectResponse(url="/ui/")' in main
