"""MCP identity binding: a verified JWT is the only source of caller identity.

The review's P0 was that the MCP server took ``user_token`` / ``gateway_token`` as tool
arguments and used a caller-supplied ``user_id``. These contract tests pin the pure identity
rules (no MCP SDK import) so they run in the minimal contract-test environment.
"""

from __future__ import annotations

import ast
import inspect
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.jwt import VerifiedToken, issue_jwt, verify_jwt
from app.mcp_auth import (
    McpAuthError,
    identity_from_verified_token,
    require_owner,
    require_scope,
    resolve_dev_identity,
    resolve_identity_from_authorization,
)

SECRET = "contract-mcp-secret-" + "s" * 32
ISSUER = "https://issuer.example.test"
AUDIENCE = "omnicare-mcp"
REQUIRED_SCOPE = "omnicare.read"

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "app_env": "test",
        "auth_mode": "jwt",
        "auth_secret": SECRET,
        "auth_jwt_issuer": ISSUER,
        "auth_jwt_audience": AUDIENCE,
        "mcp_required_scopes": REQUIRED_SCOPE,
    }
    base.update(overrides)
    return Settings(**base)


def token(subject: str = "alice", *, cfg: Settings | None = None, scopes=(), **kwargs) -> str:
    return issue_jwt(
        subject,
        settings=cfg or settings(),
        scopes=list(scopes) or [REQUIRED_SCOPE],
        **kwargs,
    )


def rejected_code(excinfo: pytest.ExceptionInfo[McpAuthError]) -> str:
    return excinfo.value.code


def test_identity_comes_from_the_verified_bearer_token() -> None:
    cfg = settings()
    raw = token("alice", cfg=cfg)

    identity = resolve_identity_from_authorization(f"Bearer {raw}", cfg)

    assert identity.user_id == "alice"
    assert identity.subject == "alice"
    assert identity.tenant_id == "default"
    assert identity.role == "user"
    assert REQUIRED_SCOPE in identity.scopes
    assert identity.issuer == ISSUER
    assert identity.token_id and identity.token_id != ""
    assert identity.auth_method == "jwt"


def test_bearer_scheme_is_mandatory() -> None:
    cfg = settings()
    for authorization in (
        None,
        "",
        "   ",
        "Basic dXNlcjpwYXNz",
        "Bearer",
        "Bearer   ",
        "Token abc",
    ):
        with pytest.raises(McpAuthError) as error:
            resolve_identity_from_authorization(authorization, cfg)
        assert rejected_code(error) == "mcp_identity_required"


def test_tampered_foreign_or_malformed_token_is_rejected() -> None:
    cfg = settings()
    foreign = token(cfg=settings(auth_secret="another-" + "z" * 40))
    malformed = "not-a-jwt"
    tampered = token("alice", cfg=cfg)[:-3] + "AAA"

    for bad in (foreign, malformed, tampered):
        with pytest.raises(McpAuthError) as error:
            resolve_identity_from_authorization(f"Bearer {bad}", cfg)
        assert rejected_code(error) == "mcp_identity_invalid"


def test_expired_and_not_yet_valid_tokens_are_rejected() -> None:
    cfg = settings()
    expired = token("alice", cfg=cfg, ttl_seconds=60, now=time.time() - 3600)
    future = token("alice", cfg=cfg, ttl_seconds=3600, now=time.time() + 3600)

    for bad in (expired, future):
        with pytest.raises(McpAuthError) as error:
            resolve_identity_from_authorization(f"Bearer {bad}", cfg)
        assert rejected_code(error) == "mcp_identity_invalid"


def test_token_must_carry_the_required_mcp_scope() -> None:
    cfg = settings()
    without_scope = token("alice", cfg=cfg, scopes=["products:read"])

    with pytest.raises(McpAuthError) as error:
        resolve_identity_from_authorization(f"Bearer {without_scope}", cfg)
    assert rejected_code(error) == "mcp_identity_invalid"


def test_required_scopes_are_read_from_settings() -> None:
    cfg = settings(mcp_required_scopes="omnicare.read omnicare.write")
    only_read = issue_jwt("alice", settings=cfg, scopes=[REQUIRED_SCOPE])
    both = issue_jwt("alice", settings=cfg, scopes=[REQUIRED_SCOPE, "omnicare.write"])

    with pytest.raises(McpAuthError):
        resolve_identity_from_authorization(f"Bearer {only_read}", cfg)
    identity = resolve_identity_from_authorization(f"Bearer {both}", cfg)
    assert set(identity.scopes) >= {REQUIRED_SCOPE, "omnicare.write"}


def test_tenant_allowlist_is_enforced() -> None:
    cfg = settings(auth_allowed_tenants="tenant-a")
    foreign_tenant = token("alice", cfg=cfg, tenant_id="tenant-b")

    with pytest.raises(McpAuthError) as error:
        resolve_identity_from_authorization(f"Bearer {foreign_tenant}", cfg)
    assert rejected_code(error) == "mcp_identity_invalid"

    allowed = token("alice", cfg=cfg, tenant_id="tenant-a")
    assert resolve_identity_from_authorization(f"Bearer {allowed}", cfg).tenant_id == "tenant-a"


def test_identity_from_verified_token_requires_a_subject() -> None:
    verified = VerifiedToken(
        subject="",
        issuer=ISSUER,
        audience=AUDIENCE,
        role="user",
        tenant_id="default",
        scopes=(REQUIRED_SCOPE,),
        jti=None,
        expires_at=datetime.now(UTC),
        claims={},
    )

    with pytest.raises(McpAuthError) as error:
        identity_from_verified_token(verified)
    assert rejected_code(error) == "mcp_identity_invalid"


def test_identity_is_mapped_from_verified_token_claims() -> None:
    cfg = settings()
    raw = issue_jwt(
        "alice",
        settings=cfg,
        role="admin",
        tenant_id="tenant-a",
        scopes=[REQUIRED_SCOPE],
        jti="token-123",
    )
    verified = verify_jwt(raw, settings=settings(auth_allowed_tenants="tenant-a"))

    identity = identity_from_verified_token(verified)

    assert identity.user_id == "alice"
    assert identity.role == "admin"
    assert identity.tenant_id == "tenant-a"
    assert identity.token_id == "token-123"


def test_scope_and_owner_checks() -> None:
    identity = resolve_identity_from_authorization(f"Bearer {token('alice')}", settings())

    require_scope(identity, REQUIRED_SCOPE)
    with pytest.raises(McpAuthError) as scope_error:
        require_scope(identity, "omnicare.admin")
    assert rejected_code(scope_error) == "mcp_scope_required"

    require_owner("alice", identity)
    with pytest.raises(McpAuthError) as owner_error:
        require_owner("bob", identity)
    assert rejected_code(owner_error) == "order_not_owned"


def test_dev_identity_falls_back_only_inside_local_dev_test() -> None:
    local = resolve_dev_identity(settings(app_env="local", auth_mode="dev", demo_user_id="demo-1"))
    assert local.user_id == "demo-1"
    assert local.auth_method == "local_demo"
    assert local.token_id is None
    assert local.scopes == (REQUIRED_SCOPE,)

    legacy_local = resolve_dev_identity(settings(app_env="test", auth_mode="token"))
    assert legacy_local.auth_method == "local_demo"

    for cfg in (
        settings(app_env="production", auth_mode="dev"),
        settings(app_env="production", auth_mode="token"),
        settings(app_env="test", auth_mode="jwt"),
        settings(app_env="test", auth_mode="oidc"),
        settings(app_env="test", auth_mode="nonsense"),
    ):
        with pytest.raises(McpAuthError) as error:
            resolve_dev_identity(cfg)
        assert rejected_code(error) == "mcp_identity_required"


def _argument_names(source: str) -> set[str]:
    """Every function argument name declared in the module (nested tools included)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = node.args
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            ):
                names.add(argument.arg)
    return names


def test_no_user_id_or_legacy_gateway_token_concepts_remain() -> None:
    authorization_params = set(inspect.signature(resolve_identity_from_authorization).parameters)
    assert authorization_params == {"authorization", "settings"}
    assert set(inspect.signature(resolve_dev_identity).parameters) == {"settings"}

    # Pure identity module: no caller-supplied identity / shared gateway-token argument.
    identity_args = _argument_names((APP_ROOT / "mcp_auth.py").read_text(encoding="utf-8"))
    assert "user_id" not in identity_args
    assert not {"user_token", "gateway_token", "mcp_auth_token"} & identity_args

    # Tools may read Order.user_id, but no tool may accept a caller-named user id or a
    # token argument (the runtime schema test proves the wire contract too).
    server_args = _argument_names((APP_ROOT / "mcp_server.py").read_text(encoding="utf-8"))
    assert "user_id" not in server_args
    assert not {"user_token", "gateway_token", "mcp_auth_token"} & server_args
