"""FastAPI dependencies for identity, authorization and app services.

Rules enforced here:

* AUTH_MODE=token: legacy self-issued HMAC bearer tokens (refused when APP_ENV=production).
* AUTH_MODE=jwt / oidc: OIDC-style JWTs verified by app.core.jwt; the tenant and scope claims
  are mapped onto the principal so downstream services can enforce tenancy.
* AUTH_MODE=dev: identity comes from X-User-Id or the configured demo user; refused when
  APP_ENV=production so a misconfiguration cannot silently disable auth.
* Admin endpoints require role admin; X-Admin-Key may elevate in dev only, and only when it
  matches ADMIN_API_KEY in constant time.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import Settings, get_settings
from app.core.jwt import JwtError, VerifiedToken, verify_jwt
from app.core.security import AuthError, Principal, matches_secret, verify_token

_JWT_MODES = frozenset({"jwt", "oidc"})
_SUPPORTED_MODES = frozenset({"dev", "token"}) | _JWT_MODES

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={"code": "authentication_required", "message": "A valid bearer token is required."},
    headers={"WWW-Authenticate": "Bearer"},
)
_FORBIDDEN = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={"code": "admin_required", "message": "Administrator role is required."},
)
_UNKNOWN_MODE = HTTPException(
    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    detail={
        "code": "insecure_auth_configuration",
        "message": "AUTH_MODE is not one of dev, token, jwt or oidc.",
    },
)


def get_supervisor(request: Request):
    return request.app.state.supervisor


def _settings_for(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def _auth_mode(settings: Settings) -> str:
    return (str(getattr(settings, "auth_mode", "") or "dev")).strip().lower() or "dev"


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_token", "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bearer_token(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _UNAUTHORIZED
    return token.strip()


def _dev_principal(settings: Settings, x_user_id: str | None) -> Principal:
    if settings.app_env.lower() == "production":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "insecure_auth_configuration",
                "message": "AUTH_MODE=dev is not allowed when APP_ENV=production.",
            },
        )
    return Principal(
        user_id=(x_user_id or settings.demo_user_id),
        role="user",
        tenant_id=settings.default_tenant_id,
    )


def _verified_principal(verified: VerifiedToken) -> Principal:
    return Principal(
        user_id=verified.subject,
        role=verified.role,
        tenant_id=verified.tenant_id,
        scopes=verified.scopes,
    )


def _principal_from_bearer(token: str, settings: Settings) -> Principal:
    """Verify a bearer token in the configured mode (never trusts client identity fields)."""
    if _auth_mode(settings) in _JWT_MODES:
        try:
            return _verified_principal(verify_jwt(token, settings=settings))
        except JwtError as error:
            raise _unauthorized(str(error)) from error
    # dev mode keeps accepting the self-issued HMAC token for local demos; token mode always
    # uses it.
    try:
        return verify_token(token, secret=settings.auth_secret)
    except AuthError as error:
        raise _unauthorized(str(error)) from error


async def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_admin_key: str | None = Header(default=None),
) -> Principal:
    settings = _settings_for(request)
    mode = _auth_mode(settings)
    if mode not in _SUPPORTED_MODES:
        raise _UNKNOWN_MODE

    if authorization:
        principal = _principal_from_bearer(_bearer_token(authorization), settings)
    elif mode == "dev":
        principal = _dev_principal(settings, x_user_id)
    else:
        raise _UNAUTHORIZED

    if (
        mode == "dev"
        and settings.admin_api_key
        and matches_secret(x_admin_key, settings.admin_api_key)
    ):
        principal = Principal(
            user_id=principal.user_id,
            role="admin",
            tenant_id=principal.tenant_id,
            scopes=principal.scopes,
        )
    return principal


def authenticate_ws(
    *, token: str | None, admin_key: str | None, settings: Settings
) -> Principal | None:
    """Authenticate the monitor socket; only admins may subscribe."""
    mode = _auth_mode(settings)
    if mode in _JWT_MODES:
        if not token:
            return None
        try:
            principal = _verified_principal(verify_jwt(token, settings=settings))
        except JwtError:
            return None
        return principal if principal.is_admin else None
    if mode == "token":
        if not token:
            return None
        try:
            principal = verify_token(token, secret=settings.auth_secret)
        except AuthError:
            return None
        return principal if principal.is_admin else None
    if mode != "dev":
        return None
    if str(getattr(settings, "app_env", "") or "").lower() == "production":
        return None
    if settings.admin_api_key and not matches_secret(admin_key, settings.admin_api_key):
        return None
    return Principal(
        user_id=settings.demo_user_id,
        role="admin",
        tenant_id=settings.default_tenant_id,
    )


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin:
        raise _FORBIDDEN
    return principal


def require_user(principal: Principal = Depends(get_principal)) -> Principal:
    return principal


__all__ = [
    "Principal",
    "authenticate_ws",
    "get_principal",
    "get_supervisor",
    "require_admin",
    "require_user",
]
