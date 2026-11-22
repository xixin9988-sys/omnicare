"""Trusted identity for the MCP server, derived from a verified JWT.

The external review's P0: the MCP tools used to accept ``user_token`` / ``gateway_token``
arguments, so identity was a caller-supplied value and the transport never authenticated
anybody. Identity now comes from the authenticated MCP transport (``Authorization: Bearer``)
and is verified by :mod:`app.core.jwt`; tools take only their business arguments.

This module is pure on purpose - it imports no MCP SDK code - so contract tests can pin the
identity rules without the runtime, and a broken MCP dependency cannot mask an auth bug.
The SDK adapters live in :mod:`app.mcp_token` / :mod:`app.mcp_server`.

Local demos keep working: ``AUTH_MODE=dev`` (and the legacy ``token`` mode) fall back to the
configured demo identity, but only for ``APP_ENV`` local/dev/test. Anything else refuses,
because an unauthenticated MCP endpoint must never be served outside a local dev box.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.jwt import JwtError, VerifiedToken, verify_jwt

#: Modes in which the MCP transport authenticates every request with a JWT.
JWT_AUTH_MODES = frozenset({"jwt", "oidc"})
#: Modes that may still fall back to the local demo identity (local/dev/test only).
LOCAL_FALLBACK_MODES = frozenset({"dev", "token"})
#: Environments where the unauthenticated demo identity is allowed.
LOCAL_ENVS = frozenset({"local", "dev", "test"})


class McpAuthError(Exception):
    """Raised when the MCP caller's identity cannot be established.

    ``code`` is a stable machine-readable value (``mcp_identity_required``,
    ``mcp_identity_invalid``, ``mcp_scope_required``, ``order_not_owned``). Messages never
    contain the raw bearer token.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class TrustedIdentity:
    """The authenticated caller of an MCP tool.

    ``user_id`` is the JWT subject (or the demo user id); it is server-derived and never a
    tool argument. ``token_id`` is the JWT ``jti`` (None for the local demo identity).
    """

    user_id: str
    tenant_id: str = "default"
    role: str = "user"
    scopes: tuple[str, ...] = ()
    issuer: str = ""
    subject: str = ""
    token_id: str | None = None
    auth_method: str = "jwt"  # "jwt" | "local_demo"


def _text(value: object) -> str:
    return str(value or "").strip()


def auth_mode(settings) -> str:
    """Return the configured auth mode (lowercase; "dev" when unset)."""
    return _text(getattr(settings, "auth_mode", "")).lower() or "dev"


def app_env(settings) -> str:
    """Return the configured environment (lowercase; "local" when unset)."""
    return _text(getattr(settings, "app_env", "")).lower() or "local"


def required_mcp_scopes(settings) -> tuple[str, ...]:
    """Scopes an MCP caller must carry (from Settings.required_mcp_scopes)."""
    scopes = getattr(settings, "required_mcp_scopes", None)
    if scopes is None:
        raw = _text(getattr(settings, "mcp_required_scopes", ""))
        scopes = raw.replace(",", " ").split()
    return tuple(str(scope).strip() for scope in scopes if str(scope).strip())


def identity_from_verified_token(verified: VerifiedToken) -> TrustedIdentity:
    """Map a verified JWT onto the trusted MCP identity (pure, no re-verification)."""
    subject = _text(verified.subject)
    if not subject:
        raise McpAuthError("mcp_identity_invalid", "The access token has no subject.")
    return TrustedIdentity(
        user_id=subject,
        tenant_id=_text(verified.tenant_id) or "default",
        role=_text(verified.role) or "user",
        scopes=tuple(verified.scopes or ()),
        issuer=_text(verified.issuer),
        subject=subject,
        token_id=verified.jti,
        auth_method="jwt",
    )


def bearer_token_from_authorization(authorization: str | None) -> str | None:
    """Extract the bearer token from an Authorization header value, or None."""
    if not authorization:
        return None
    scheme, _, token = str(authorization).partition(" ")
    if scheme.strip().lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def resolve_identity_from_authorization(authorization: str | None, settings) -> TrustedIdentity:
    """Verify the Authorization header and return the trusted identity.

    A ``Bearer`` JWT is mandatory here: this is the identity path for the authenticated
    (jwt/oidc) MCP transport. Verification failure maps to ``mcp_identity_required`` (no
    usable credential) or ``mcp_identity_invalid`` (rejected token), and the error message
    never echoes the token.
    """
    token = bearer_token_from_authorization(authorization)
    if token is None:
        raise McpAuthError(
            "mcp_identity_required",
            "A Bearer access token in the Authorization header is required.",
        )
    try:
        verified = verify_jwt(
            token,
            settings=settings,
            required_scopes=required_mcp_scopes(settings),
        )
    except JwtError as error:
        raise McpAuthError(
            "mcp_identity_invalid", f"The bearer token was rejected: {error}"
        ) from error
    return identity_from_verified_token(verified)


def local_demo_allowed(settings) -> bool:
    """True when the unauthenticated local demo identity may be used."""
    return auth_mode(settings) in LOCAL_FALLBACK_MODES and app_env(settings) in LOCAL_ENVS


def resolve_dev_identity(settings) -> TrustedIdentity:
    """Return the local demo identity, only for AUTH_MODE=dev/token in local/dev/test.

    This is the fallback for local demos without an identity provider. Outside local/dev/test
    - and in jwt/oidc modes, where a real token exists - it fails closed.
    """
    mode = auth_mode(settings)
    if mode not in LOCAL_FALLBACK_MODES:
        raise McpAuthError(
            "mcp_identity_required",
            f"AUTH_MODE={mode!r} requires a verified Bearer JWT; "
            "the local demo identity is only for AUTH_MODE=dev/token.",
        )
    env = app_env(settings)
    if env not in LOCAL_ENVS:
        raise McpAuthError(
            "mcp_identity_required",
            f"The local demo identity is only allowed for APP_ENV in {sorted(LOCAL_ENVS)}, "
            f"not {env!r}.",
        )
    user_id = _text(getattr(settings, "demo_user_id", "")) or "demo_user_001"
    return TrustedIdentity(
        user_id=user_id,
        tenant_id=_text(getattr(settings, "default_tenant_id", "")) or "default",
        role="user",
        scopes=required_mcp_scopes(settings),
        issuer="",
        subject=user_id,
        token_id=None,
        auth_method="local_demo",
    )


def require_scope(identity: TrustedIdentity, scope: str) -> None:
    """Raise unless the identity carries scope."""
    required = _text(scope)
    if required and required not in identity.scopes:
        raise McpAuthError(
            "mcp_scope_required",
            f"The caller is missing the required scope {required!r}.",
        )


def require_owner(order_user_id: str, identity: TrustedIdentity) -> None:
    """Raise unless the order belongs to the authenticated customer."""
    if order_user_id != identity.user_id:
        raise McpAuthError("order_not_owned", "This order does not belong to the caller.")


__all__ = [
    "JWT_AUTH_MODES",
    "LOCAL_ENVS",
    "LOCAL_FALLBACK_MODES",
    "McpAuthError",
    "TrustedIdentity",
    "app_env",
    "auth_mode",
    "bearer_token_from_authorization",
    "identity_from_verified_token",
    "local_demo_allowed",
    "require_owner",
    "require_scope",
    "required_mcp_scopes",
    "resolve_dev_identity",
    "resolve_identity_from_authorization",
]
