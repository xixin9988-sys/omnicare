"""Authentication primitives: signed bearer tokens and a principal model.

The API must never take ``user_id`` from a request body. Callers present a token; the token's
signature (HMAC-SHA256) is verified against ``AUTH_SECRET`` and the resulting principal is the
only source of identity. ``AUTH_MODE=dev`` exists for local demos/tests and is refused when
``APP_ENV=production``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field


class AuthError(Exception):
    """Raised when a token is missing, malformed, expired or has a bad signature."""


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    role: str = "user"
    tenant_id: str = "default"
    scopes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return _b64encode(digest)


def issue_token(
    user_id: str,
    *,
    secret: str,
    role: str = "user",
    tenant_id: str = "default",
    ttl_seconds: int = 3600,
    now: int | None = None,
) -> str:
    """Create ``<payload>.<signature>`` for a principal. Never log the result."""
    if not secret:
        raise AuthError("AUTH_SECRET is not configured")
    issued_at = int(now if now is not None else time.time())
    payload = {
        "sub": user_id,
        "role": role,
        "tenant": tenant_id,
        "iat": issued_at,
        "exp": issued_at + int(ttl_seconds),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{_b64encode(raw)}.{_sign(raw, secret)}"


def verify_token(token: str, *, secret: str, now: int | None = None) -> Principal:
    if not secret:
        raise AuthError("AUTH_SECRET is not configured")
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        raw = _b64decode(encoded_payload)
        signature = _b64decode(encoded_signature)
    except (ValueError, TypeError) as error:
        raise AuthError("malformed token") from error

    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise AuthError("bad signature")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise AuthError("malformed payload") from error

    expires_at = int(payload.get("exp", 0))
    current = int(now if now is not None else time.time())
    if expires_at <= current:
        raise AuthError("token expired")

    user_id = str(payload.get("sub") or "")
    if not user_id:
        raise AuthError("token has no subject")
    return Principal(
        user_id=user_id,
        role=str(payload.get("role") or "user"),
        tenant_id=str(payload.get("tenant") or "default"),
    )


def matches_secret(candidate: str | None, expected: str) -> bool:
    """Constant-time comparison for admin keys."""
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
