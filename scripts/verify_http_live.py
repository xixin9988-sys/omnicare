"""Live acceptance probe for the OmniCare HTTP API with JWT identity.

Usage (from the repo root, project venv):

    export APP_ENV=local AUTH_MODE=jwt AUTH_SECRET=<32+ chars>
    export AUTH_JWT_ISSUER=http://127.0.0.1:8000 AUTH_JWT_AUDIENCE=omnicare-api
    uvicorn app.main:app --host 127.0.0.1 --port 8001
    python scripts/verify_http_live.py

The probe issues its own tokens through app.auth_cli and exercises the 401/403 matrix,
session/chat identity binding, admin role checks and the memory_written provenance event."""

import subprocess
import sys

import httpx

BASE = "http://127.0.0.1:8001"
py = sys.executable

py = sys.executable


def issue(user, role="user"):
    out = subprocess.run(
        [
            py,
            "-m",
            "app.auth_cli",
            "issue-token",
            "--user",
            user,
            "--role",
            role,
            "--scopes",
            "omnicare.read",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


user_tok = issue("demo_user_001")
admin_tok = issue("admin_user", "admin")

results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(("PASS " if cond else "FAIL ") + name + (" :: " + str(detail) if detail else ""))


with httpx.Client(base_url=BASE, timeout=20) as c:
    r = c.get("/health")
    check(
        "root /health alias answers",
        r.status_code == 200 and r.json().get("status") == "ok",
        r.status_code,
    )
    check("prefixed /api/v1/health answers", c.get("/api/v1/health").status_code == 200)

    check("no token -> 401", c.post("/api/v1/sessions", json={}).status_code == 401)
    check(
        "X-User-Id alone is ignored -> 401",
        c.post("/api/v1/sessions", json={}, headers={"X-User-Id": "demo_user_001"}).status_code
        == 401,
    )

    created = c.post(
        "/api/v1/sessions",
        json={"user_id": "demo_user_001"},
        headers={"Authorization": f"Bearer {user_tok}"},
    )
    check("JWT creates a session", created.status_code == 201, created.status_code)
    session_id = created.json()["session_id"]

    spoof = c.post(
        "/api/v1/chat",
        json={"user_id": "intruder", "session_id": session_id, "message": "hi"},
        headers={"Authorization": f"Bearer {user_tok}"},
    )
    check("spoofed body user_id -> 403", spoof.status_code == 403, spoof.status_code)

    chat = c.post(
        "/api/v1/chat",
        json={"session_id": session_id, "message": "我的订单 ORD-2026-0001 物流到哪了？"},
        headers={"Authorization": f"Bearer {user_tok}"},
    )
    body = chat.json() if chat.status_code == 200 else {}
    check(
        "chat without body user_id works (identity from token)",
        chat.status_code == 200,
        chat.status_code,
    )
    check(
        "logistics reply carries the tracking number",
        "SF1234567890" in body.get("reply", ""),
        body.get("reply", "")[:60],
    )

    check(
        "user JWT on admin endpoint -> 403",
        c.get("/api/v1/events", headers={"Authorization": f"Bearer {user_tok}"}).status_code == 403,
    )
    check(
        "admin JWT on admin endpoint -> 200",
        c.get("/api/v1/events", headers={"Authorization": f"Bearer {admin_tok}"}).status_code
        == 200,
    )

    bad = c.post("/api/v1/sessions", json={}, headers={"Authorization": "Bearer not.a.jwt"})
    check("garbage JWT -> 401", bad.status_code == 401, bad.status_code)

    pref = c.post(
        "/api/v1/chat",
        json={"session_id": session_id, "message": "以后不要用顺丰，优先用京东物流"},
        headers={"Authorization": f"Bearer {user_tok}"},
    )
    check("preference chat accepted", pref.status_code == 200, pref.status_code)
    events = c.get(
        "/api/v1/events?limit=50", headers={"Authorization": f"Bearer {admin_tok}"}
    ).json()
    written = [e for e in events if e.get("event_type") == "memory_written"]
    check(
        "memory_written event carries provenance ref",
        bool(written) and "source_ref" in written[0].get("payload", {}),
        written[0].get("payload", {}) if written else "none",
    )

print(
    f"\n{sum(1 for _, ok, _ in results if ok)} passed, {sum(1 for _, ok, _ in results if not ok)} failed"
)
raise SystemExit(0 if all(ok for _, ok, _ in results) else 1)
