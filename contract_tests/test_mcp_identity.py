"""MCP identity binding (R-02): the gateway token must not stand in for a customer."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.security import issue_token
from app.mcp_auth import McpAuthError, require_owner, resolve_identity

SECRET = "s" * 32


def settings(**overrides) -> Settings:
    base = {"_env_file": None, "app_env": "local", "auth_mode": "dev", "auth_secret": SECRET}
    base.update(overrides)
    return Settings(**base)


def test_identity_comes_from_the_signed_token() -> None:
    token = issue_token("alice", secret=SECRET)
    identity = resolve_identity(user_token=token, gateway_token=None, settings=settings())
    assert identity.user_id == "alice"
    assert identity.source == "token"


def test_no_user_id_parameter_exists_to_spoof_another_customer() -> None:
    import inspect

    from app.mcp_auth import resolve_identity as fn

    parameters = inspect.signature(fn).parameters
    assert "user_id" not in parameters


def test_tampered_token_is_rejected() -> None:
    token = issue_token("alice", secret="another-secret")
    with pytest.raises(McpAuthError) as error:
        resolve_identity(user_token=token, gateway_token=None, settings=settings())
    assert error.value.code == "mcp_identity_invalid"


def test_gateway_token_is_required_when_configured() -> None:
    cfg = settings(mcp_auth_token="gateway-secret")
    with pytest.raises(McpAuthError) as error:
        resolve_identity(user_token=None, gateway_token="wrong", settings=cfg)
    assert error.value.code == "mcp_gateway_auth_failed"


def test_gateway_token_alone_does_not_identify_a_customer_outside_local_dev() -> None:
    cfg = settings(
        app_env="production",
        auth_mode="token",
        mcp_auth_token="gateway-secret",
    )
    with pytest.raises(McpAuthError) as error:
        resolve_identity(user_token=None, gateway_token="gateway-secret", settings=cfg)
    assert error.value.code == "mcp_identity_required"


def test_local_dev_falls_back_to_the_demo_identity() -> None:
    identity = resolve_identity(user_token=None, gateway_token=None, settings=settings())
    assert identity.source == "local_demo"
    assert identity.user_id == settings().demo_user_id


def test_owner_check_refuses_someone_elses_order() -> None:
    token = issue_token("alice", secret=SECRET)
    identity = resolve_identity(user_token=token, gateway_token=None, settings=settings())
    require_owner("alice", identity)
    with pytest.raises(McpAuthError) as error:
        require_owner("bob", identity)
    assert error.value.code == "order_not_owned"
