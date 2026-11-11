"""FastAPI dependencies for identity, authorization and app services.

Rules enforced here:

* ``AUTH_MODE=token`` (required outside dev): identity comes only from a signed bearer token.
* ``AUTH_MODE=dev``: identity comes from ``X-User-Id`` or the configured demo user; refused
  when ``APP_ENV=production`` so a misconfiguration cannot silently disable auth.
* Admin endpoints require role ``admin``; ``X-Admin-Key`` may elevate in dev only, and only
  when it matches ``ADMIN_API_KEY`` in constant time.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import Settings, get_settings
from app.core.security import AuthError, Principal, matches_secret, verify_token

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={"code": "authentication_required", "message": "A valid bearer token is required."},
    headers={"WWW-Authenticate": "Bearer"},
)
_FORBIDDEN = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={"code": "admin_required", "message": "Administrator role is required."},
)


def get_supervisor(request: Request):
    return request.app.state.supervisor


def _settings_for(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def _dev_principal(settings: Settings, x_user_id: str | None) -> Principal:
    if settings.app_env.lower() == "production" and settings.auth_mode == "dev":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "insecure_auth_configuration",
                "message": "AUTH_MODE=dev is not allowed when APP_ENV=production.",
            },
        )
    return Principal(user_id=(x_user_id or settings.demo_user_id), role="user")


async def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_admin_key: str | None = Header(default=None),
) -> Principal:
    settings = _settings_for(request)

    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise _UNAUTHORIZED
        try:
            principal = verify_token(token, secret=settings.auth_secret)
        except AuthError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "invalid_token", "message": str(error)},
                headers={"WWW-Authenticate": "Bearer"},
            ) from error
    elif settings.auth_mode == "dev":
        principal = _dev_principal(settings, x_user_id)
    else:
        raise _UNAUTHORIZED

    if (
        settings.auth_mode == "dev"
        and settings.admin_api_key
        and matches_secret(x_admin_key, settings.admin_api_key)
    ):
        principal = Principal(
            user_id=principal.user_id,
            role="admin",
            tenant_id=principal.tenant_id,
        )
    return principal


def authenticate_ws(
    *, token: str | None, admin_key: str | None, settings: Settings
) -> Principal | None:
    """Authenticate the monitor socket; only admins may subscribe."""
    if settings.auth_mode == "token":
        if not token:
            return None
        try:
            principal = verify_token(token, secret=settings.auth_secret)
        except AuthError:
            return None
    else:
        if settings.app_env.lower() == "production":
            return None
        if settings.admin_api_key and not matches_secret(admin_key, settings.admin_api_key):
            return None
        principal = Principal(user_id=settings.demo_user_id, role="admin")
    return principal if principal.is_admin else None


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
