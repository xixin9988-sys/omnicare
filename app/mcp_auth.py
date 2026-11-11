"""Identity binding for the standalone MCP server.

R-02 (review): the MCP endpoint accepted a shared token plus a caller-supplied ``user_id``.
A shared secret proves *someone is allowed to call the gateway*; it does not prove *which
customer* is calling, so a client could read another customer's order by naming them.

This module derives the customer from a signed token instead. The gateway token (if set) is
still required as transport-level authentication, but it can no longer stand in for identity.
Pure functions only — the MCP SDK is imported by ``app.mcp_server``, not here.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.security import AuthError, matches_secret, verify_token

_LOCAL_ENVS = frozenset({"local", "dev", "test"})


class McpAuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class McpIdentity:
    user_id: str
    role: str = "user"
    source: str = "token"  # "token" | "local_demo"


def resolve_identity(*, user_token: str | None, gateway_token: str | None, settings) -> McpIdentity:
    """Return the caller's identity, or raise :class:`McpAuthError`.

    Order of checks:
    1. gateway token, when ``MCP_AUTH_TOKEN`` is configured (constant-time compare);
    2. signed user token — the only trusted source of ``user_id``;
    3. the local demo identity, only for ``AUTH_MODE=dev`` outside production-like envs.
    """
    expected_gateway = str(getattr(settings, "mcp_auth_token", "") or "")
    if expected_gateway and not matches_secret(gateway_token, expected_gateway):
        raise McpAuthError(
            "mcp_gateway_auth_failed",
            "The MCP gateway token is missing or wrong.",
        )

    if user_token:
        try:
            principal = verify_token(user_token, secret=str(getattr(settings, "auth_secret", "")))
        except AuthError as error:
            raise McpAuthError("mcp_identity_invalid", f"User token rejected: {error}") from error
        return McpIdentity(user_id=principal.user_id, role=principal.role, source="token")

    auth_mode = str(getattr(settings, "auth_mode", "dev")).lower()
    app_env = str(getattr(settings, "app_env", "local")).lower()
    if auth_mode == "dev" and app_env in _LOCAL_ENVS:
        return McpIdentity(user_id=str(settings.demo_user_id), role="user", source="local_demo")

    raise McpAuthError(
        "mcp_identity_required",
        "A signed user token is required; the gateway token does not identify a customer.",
    )


def require_owner(order_user_id: str, identity: McpIdentity) -> None:
    """Raise unless the order belongs to the authenticated customer."""
    if order_user_id != identity.user_id:
        raise McpAuthError("order_not_owned", "This order does not belong to the caller.")


__all__ = ["McpAuthError", "McpIdentity", "require_owner", "resolve_identity"]
