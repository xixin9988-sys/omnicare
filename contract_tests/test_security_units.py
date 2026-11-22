"""Security regressions for V-01..V-08 plus the JWT/OIDC auth core (no DB stack needed)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.deps import authenticate_ws, get_principal
from app.auth_cli import main as cli_main
from app.core.config import Settings, get_settings
from app.core.jwt import issue_jwt, verify_jwt
from app.core.security import AuthError, Principal, issue_token, verify_token
from app.memory.local_adapter import LocalFileMemoryAdapter

SECRET = "s" * 64


def jwt_settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "app_env": "local",
        "auth_mode": "jwt",
        "auth_secret": SECRET,
        "auth_jwt_issuer": "omnicare",
        "auth_jwt_audience": "omnicare-api",
        "auth_jwt_leeway_seconds": 0,
    }
    base.update(overrides)
    return Settings(**base)


def fake_request(settings: Settings):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))


def set_settings_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


# ------------------------------------------------------------------ legacy HMAC mode
def test_token_round_trip_and_tamper() -> None:
    token = issue_token("alice", secret="s3cret", role="admin", now=1000)
    principal = verify_token(token, secret="s3cret", now=1001)
    assert principal == Principal(user_id="alice", role="admin")

    with pytest.raises(AuthError):
        verify_token(token, secret="other", now=1001)

    _, signature = token.split(".")
    forged_payload = issue_token("mallory", secret="s3cret", now=1000).split(".")[0]
    with pytest.raises(AuthError):
        verify_token(f"{forged_payload}.{signature}", secret="s3cret", now=1001)


def test_token_expiry() -> None:
    token = issue_token("alice", secret="s3cret", ttl_seconds=10, now=1000)
    with pytest.raises(AuthError):
        verify_token(token, secret="s3cret", now=1011)


def test_dev_websocket_requires_admin_key_when_configured() -> None:
    settings = Settings(auth_mode="dev", admin_api_key="admin-key", app_env="local")
    assert authenticate_ws(token=None, admin_key=None, settings=settings) is None
    assert authenticate_ws(token=None, admin_key="wrong", settings=settings) is None
    principal = authenticate_ws(token=None, admin_key="admin-key", settings=settings)
    assert principal is not None and principal.is_admin


def test_token_mode_rejects_missing_or_bad_token() -> None:
    settings = Settings(auth_mode="token", auth_secret="s3cret", app_env="local")
    assert authenticate_ws(token=None, admin_key=None, settings=settings) is None
    assert authenticate_ws(token="garbage", admin_key=None, settings=settings) is None
    admin = issue_token("root", secret="s3cret", role="admin")
    assert authenticate_ws(token=admin, admin_key=None, settings=settings).is_admin
    user = issue_token("alice", secret="s3cret", role="user")
    assert authenticate_ws(token=user, admin_key=None, settings=settings) is None


# ------------------------------------------------------------------ JWT / OIDC modes
@pytest.mark.parametrize("auth_mode", ["jwt", "oidc"])
def test_jwt_modes_map_the_verified_token_to_the_principal(auth_mode: str) -> None:
    cfg = jwt_settings(auth_mode=auth_mode)
    token = issue_jwt(
        "alice",
        settings=cfg,
        role="admin",
        tenant_id="tenant-a",
        scopes=("omnicare.read",),
    )
    principal = asyncio.run(
        get_principal(
            fake_request(cfg),
            authorization=f"Bearer {token}",
            x_user_id=None,
            x_admin_key=None,
        )
    )
    assert principal == Principal(
        user_id="alice",
        role="admin",
        tenant_id="tenant-a",
        scopes=("omnicare.read",),
    )


@pytest.mark.parametrize("auth_mode", ["jwt", "oidc"])
def test_jwt_modes_require_a_valid_bearer_token(auth_mode: str) -> None:
    cfg = jwt_settings(auth_mode=auth_mode)
    for authorization in (None, "Bearer garbage", "Basic abc"):
        with pytest.raises(HTTPException) as error:
            asyncio.run(
                get_principal(
                    fake_request(cfg),
                    authorization=authorization,
                    x_user_id="alice",
                    x_admin_key=None,
                )
            )
        assert error.value.status_code == 401
        assert error.value.headers == {"WWW-Authenticate": "Bearer"}
        assert "garbage" not in str(error.value.detail)


def test_jwt_mode_does_not_honour_the_dev_admin_key() -> None:
    cfg = jwt_settings(admin_api_key="admin-key")
    token = issue_jwt("alice", settings=cfg, role="user")
    principal = asyncio.run(
        get_principal(
            fake_request(cfg),
            authorization=f"Bearer {token}",
            x_user_id="alice",
            x_admin_key="admin-key",
        )
    )
    assert principal.role == "user"
    assert not principal.is_admin


@pytest.mark.parametrize("auth_mode", ["jwt", "oidc"])
def test_authenticate_ws_accepts_only_admin_jwts(auth_mode: str) -> None:
    cfg = jwt_settings(auth_mode=auth_mode)
    admin = issue_jwt("root", settings=cfg, role="admin")
    user = issue_jwt("alice", settings=cfg, role="user")
    accepted = authenticate_ws(token=admin, admin_key=None, settings=cfg)
    assert accepted is not None and accepted.is_admin
    assert authenticate_ws(token=user, admin_key=None, settings=cfg) is None
    assert authenticate_ws(token="garbage", admin_key=None, settings=cfg) is None
    assert authenticate_ws(token=None, admin_key="admin-key", settings=cfg) is None


# ------------------------------------------------------------------ issue-token CLI
def test_auth_cli_prints_only_a_verifiable_jwt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    set_settings_env(
        monkeypatch,
        APP_ENV="local",
        AUTH_MODE="jwt",
        AUTH_SECRET=SECRET,
        AUTH_JWT_ISSUER="omnicare",
        AUTH_JWT_AUDIENCE="omnicare-api",
        AUTH_JWT_LEEWAY_SECONDS="0",
    )
    try:
        cfg = get_settings()
        exit_code = cli_main(
            [
                "issue-token",
                "--user",
                "alice",
                "--role",
                "user",
                "--scopes",
                "omnicare.read products:read",
                "--ttl",
                "3600",
            ]
        )
    finally:
        get_settings.cache_clear()
    captured = capsys.readouterr()
    assert exit_code == 0
    token = captured.out.strip()
    assert token.count(".") == 2
    assert token not in captured.err
    verified = verify_jwt(token, settings=cfg)
    assert verified.subject == "alice"
    assert verified.role == "user"
    assert verified.scopes == ("omnicare.read", "products:read")


def test_auth_cli_refuses_to_issue_tokens_in_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    set_settings_env(monkeypatch, APP_ENV="production", AUTH_MODE="jwt", AUTH_SECRET=SECRET)
    try:
        exit_code = cli_main(["issue-token", "--user", "alice"])
    finally:
        get_settings.cache_clear()
    captured = capsys.readouterr()
    assert exit_code != 0
    assert captured.out == ""
    assert "production" in captured.err.lower()


def test_auth_cli_keeps_working_for_the_legacy_token_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    set_settings_env(monkeypatch, APP_ENV="local", AUTH_MODE="token", AUTH_SECRET=SECRET)
    try:
        exit_code = cli_main(["issue-token", "--user", "alice", "--role", "admin"])
    finally:
        get_settings.cache_clear()
    captured = capsys.readouterr()
    assert exit_code == 0
    principal = verify_token(captured.out.strip(), secret=SECRET)
    assert principal.user_id == "alice"
    assert principal.role == "admin"


# ------------------------------------------------------------------ unrelated regression
def test_memory_files_do_not_collide_for_sanitised_ids(tmp_path) -> None:
    adapter = LocalFileMemoryAdapter(storage_dir=str(tmp_path))
    first = adapter._path("alice@example.com")
    second = adapter._path("alice_example.com")
    assert first != second, "sanitising is not isolation"
    assert first.name.endswith(".jsonl")
    assert len(first.stem) == 32
