"""MCP SDK adapter: verify Bearer JWTs at the transport boundary.

``OmniCareTokenVerifier`` implements ``mcp.server.auth.provider.TokenVerifier``; the MCP
streamable-HTTP transport wraps every request in Starlette's ``AuthenticationMiddleware``
with this verifier, so a tool never sees an unauthenticated request. The raw token is handed
to the SDK (it has to be, for ``AccessToken.token``), but this module never logs it and never
returns it from a tool.

``trusted_identity_from_access_token`` turns the SDK's ``AccessToken`` back into the pure
``TrustedIdentity`` and re-checks the required scopes and tenant allowlist server-side.
"""

from __future__ import annotations

from mcp.server.auth.provider import AccessToken, TokenVerifier

from app.core.jwt import JwtError, VerifiedToken, verify_jwt
from app.mcp_auth import McpAuthError, TrustedIdentity, required_mcp_scopes

#: Fallback client id when the token has no `azp` claim (plain self-issued JWTs).
CLIENT_ID_FALLBACK = "omnicare-mcp-client"


class OmniCareTokenVerifier(TokenVerifier):
    """Verify an MCP Bearer token with the shared JWT/OIDC rules.

    Invalid, expired, wrongly signed, wrongly issued or under-scoped tokens return None,
    which the MCP auth middleware turns into a 401. Nothing is logged or echoed.
    """

    def __init__(self, settings) -> None:
        self._settings = settings

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            verified = verify_jwt(
                token,
                settings=self._settings,
                required_scopes=required_mcp_scopes(self._settings),
            )
        except JwtError:
            # Fail closed. The reason is deliberately not surfaced here (and never the token).
            return None
        return _access_token_from_verified(token, verified, self._settings)


def _access_token_from_verified(token: str, verified: VerifiedToken, settings) -> AccessToken:
    claims = verified.claims or {}
    azp = str(claims.get("azp") or "").strip() or None
    resource = str(getattr(settings, "mcp_resource_url", "") or "").strip() or None
    return AccessToken(
        token=token,
        client_id=azp or CLIENT_ID_FALLBACK,
        scopes=list(verified.scopes),
        expires_at=int(verified.expires_at.timestamp()),
        resource=resource,
        subject=verified.subject,
        claims={
            "iss": verified.issuer,
            "role": verified.role,
            "tenant_id": verified.tenant_id,
            "jti": verified.jti,
            "azp": azp,
        },
    )


def trusted_identity_from_access_token(
    access_token: AccessToken | None, settings
) -> TrustedIdentity:
    """Turn the transport's verified AccessToken into the tool-facing identity.

    Re-checks the required scopes and the tenant allowlist so a verifier bug cannot widen the
    caller's rights. Raises McpAuthError (never includes the raw token).
    """
    if access_token is None:
        raise McpAuthError(
            "mcp_identity_required",
            "The MCP request has no verified access token.",
        )
    claims = access_token.claims or {}
    subject = str(access_token.subject or "").strip()
    if not subject:
        raise McpAuthError("mcp_identity_invalid", "The access token has no subject.")

    granted = tuple(str(scope) for scope in (access_token.scopes or ()))
    missing = [scope for scope in required_mcp_scopes(settings) if scope not in granted]
    if missing:
        raise McpAuthError(
            "mcp_identity_invalid",
            "The access token is missing required scope(s): " + ", ".join(missing),
        )

    tenant_id = str(claims.get("tenant_id") or "").strip() or str(
        getattr(settings, "default_tenant_id", "") or "default"
    )
    allowed_tenants = frozenset(getattr(settings, "allowed_tenants", ()) or ())
    if allowed_tenants and tenant_id not in allowed_tenants:
        raise McpAuthError("mcp_identity_invalid", "The access token tenant is not allowed.")

    token_id = str(claims.get("jti") or "").strip() or None
    return TrustedIdentity(
        user_id=subject,
        tenant_id=tenant_id,
        role=str(claims.get("role") or "user"),
        scopes=granted,
        issuer=str(claims.get("iss") or ""),
        subject=subject,
        token_id=token_id,
        auth_method="jwt",
    )


__all__ = [
    "CLIENT_ID_FALLBACK",
    "OmniCareTokenVerifier",
    "trusted_identity_from_access_token",
]
