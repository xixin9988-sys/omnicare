"""Security regressions for V-01..V-08 (runnable without the database stack)."""

from __future__ import annotations

import pytest

from app.api.deps import authenticate_ws
from app.core.config import Settings
from app.core.security import AuthError, Principal, issue_token, verify_token
from app.memory.local_adapter import LocalFileMemoryAdapter


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


def test_memory_files_do_not_collide_for_sanitised_ids(tmp_path) -> None:
    adapter = LocalFileMemoryAdapter(storage_dir=str(tmp_path))
    first = adapter._path("alice@example.com")
    second = adapter._path("alice_example.com")
    assert first != second, "sanitising is not isolation"
    assert first.name.endswith(".jsonl")
    assert len(first.stem) == 32
