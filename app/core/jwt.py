"""OIDC/JWT access tokens: local issuing and fail-closed verification.

Two token families are supported:

* HMAC (HS256/HS384/HS512) signed with AUTH_SECRET - local issuance, tests and
  self-hosted deployments.
* Asymmetric (RS256/ES256) verified with AUTH_JWT_PUBLIC_KEY,
  AUTH_JWT_PUBLIC_KEY_PATH or AUTH_JWKS_URL (OIDC providers, kid rotation).

The accepted algorithm is pinned by AUTH_JWT_ALGORITHM: a header carrying any other
algorithm - including alg=none - is refused before key material is touched, which closes
the classic algorithm-confusion attack. Issuer, audience, tenant allowlist, jti revocation
and required scopes are enforced here as well.

Tokens are never logged and JwtError messages never echo the raw token.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import jwt as pyjwt
from jwt import PyJWKClient

HMAC_ALGORITHMS: frozenset[str] = frozenset({"HS256", "HS384", "HS512"})
ASYMMETRIC_ALGORITHMS: frozenset[str] = frozenset({"RS256", "ES256"})
SUPPORTED_ALGORITHMS: frozenset[str] = HMAC_ALGORITHMS | ASYMMETRIC_ALGORITHMS


class JwtError(Exception):
    """Raised when a JWT is missing, malformed, invalid or not acceptable.

    The code attribute lets callers (the MCP server, the HTTP layer) map failures to stable
    error codes without parsing the human-readable message.
    """

    def __init__(self, message: str, *, code: str = "invalid_token") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    """The trusted identity extracted from a verified JWT.

    expires_at is a timezone-aware UTC datetime; claims is the decoded payload (custom
    claims included) for callers that need more than the standard fields.
    """

    subject: str
    issuer: str
    audience: str
    role: str
    tenant_id: str
    scopes: tuple[str, ...]
    jti: str | None
    expires_at: datetime
    claims: Mapping[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ JWKS cache
@lru_cache(maxsize=8)
def _jwks_client(url: str) -> PyJWKClient:
    """Return the process-wide PyJWKClient for url (cached; fetches JWKS lazily)."""
    return PyJWKClient(url, cache_keys=True)


def clear_jwks_cache() -> None:
    """Drop cached JWKS clients (tests, or key rotation that must not wait for TTL)."""
    _jwks_client.cache_clear()


# ------------------------------------------------------------------ helpers
def _setting(settings, name: str, default: Any = "") -> Any:
    value = getattr(settings, name, default)
    return default if value is None else value


def _configured_algorithm(settings) -> str:
    raw = str(_setting(settings, "auth_jwt_algorithm", "") or "").strip().upper() or "HS256"
    if raw not in SUPPORTED_ALGORITHMS:
        raise JwtError(
            f"AUTH_JWT_ALGORITHM={raw!r} is not supported; use one of "
            f"{sorted(SUPPORTED_ALGORITHMS)}",
            code="unsupported_algorithm",
        )
    return raw


def _epoch(now: float | datetime | None) -> int:
    if now is None:
        return int(time.time())
    if isinstance(now, datetime):
        moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        return int(moment.timestamp())
    return int(now)


def _leeway(settings) -> int:
    try:
        value = int(_setting(settings, "auth_jwt_leeway_seconds", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def _scope_tuple(value: Iterable[str] | str | None) -> tuple[str, ...]:
    """Normalise scopes from a space/comma separated string or an iterable."""
    if value is None:
        return ()
    parts: list[str] = []
    if isinstance(value, str):
        parts.extend(value.replace(",", " ").split())
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            parts.extend(str(item).replace(",", " ").split())
    else:
        parts.extend(str(value).replace(",", " ").split())
    scopes: list[str] = []
    for scope in parts:
        if scope and scope not in scopes:
            scopes.append(scope)
    return tuple(scopes)


def _unverified_header(token: str) -> Mapping[str, Any]:
    try:
        header = pyjwt.get_unverified_header(token)
    except pyjwt.PyJWTError as error:
        raise JwtError("malformed JWT", code="malformed_token") from error
    if not isinstance(header, Mapping):
        raise JwtError("malformed JWT header", code="malformed_token")
    return header


def _translate(error: pyjwt.PyJWTError) -> JwtError:
    """Map PyJWT failures to JwtError without leaking the token."""
    mapping: tuple[tuple[type[Exception], str], ...] = (
        (pyjwt.InvalidSignatureError, "bad_signature"),
        (pyjwt.InvalidIssuerError, "wrong_issuer"),
        (pyjwt.InvalidAudienceError, "wrong_audience"),
        (pyjwt.ExpiredSignatureError, "expired_token"),
        (pyjwt.ImmatureSignatureError, "token_not_yet_valid"),
        (pyjwt.InvalidIssuedAtError, "token_not_yet_valid"),
        (pyjwt.InvalidAlgorithmError, "unsupported_algorithm"),
        (pyjwt.MissingRequiredClaimError, "missing_claim"),
        (pyjwt.InvalidKeyError, "jwt_key_invalid"),
        (pyjwt.DecodeError, "malformed_token"),
    )
    code = "invalid_token"
    for error_type, candidate in mapping:
        if isinstance(error, error_type):
            code = candidate
            break
    return JwtError(f"JWT rejected: {error}", code=code)


def _numeric_claim(payload: Mapping[str, Any], name: str) -> float | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JwtError(f"JWT claim {name!r} is not numeric", code="malformed_token")
    return float(value)


def _audience_string(value: Any, configured: str) -> str:
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [str(item) for item in value]
        if configured and configured in items:
            return configured
        return items[0] if items else ""
    return str(value or "")


def _merge_scopes(payload: Mapping[str, Any]) -> tuple[str, ...]:
    combined = _scope_tuple(payload.get("scope")) + _scope_tuple(payload.get("scopes"))
    scopes: list[str] = []
    for scope in combined:
        if scope not in scopes:
            scopes.append(scope)
    return tuple(scopes)


def _asymmetric_key(token: str, settings) -> Any:
    jwks_url = str(_setting(settings, "auth_jwks_url") or "").strip()
    if jwks_url:
        try:
            signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        except (pyjwt.PyJWKClientError, pyjwt.PyJWTError, OSError, ValueError, KeyError) as error:
            raise JwtError(
                "JWT signing key could not be resolved from AUTH_JWKS_URL",
                code="signing_key_unavailable",
            ) from error
        return signing_key.key

    public_key = str(_setting(settings, "auth_jwt_public_key") or "").strip()
    if not public_key:
        path = str(_setting(settings, "auth_jwt_public_key_path") or "").strip()
        if path:
            try:
                public_key = Path(path).expanduser().read_text(encoding="utf-8").strip()
            except OSError as error:
                raise JwtError(
                    "AUTH_JWT_PUBLIC_KEY_PATH could not be read",
                    code="jwt_not_configured",
                ) from error
    if not public_key:
        raise JwtError(
            "no JWT public key or JWKS URL is configured for asymmetric tokens",
            code="jwt_not_configured",
        )
    return public_key


def _signing_key(token: str, *, algorithm: str, settings) -> Any:
    if algorithm in HMAC_ALGORITHMS:
        secret = str(_setting(settings, "auth_secret") or "")
        if not secret:
            raise JwtError(
                "AUTH_SECRET is not configured for HMAC-signed JWTs",
                code="jwt_not_configured",
            )
        return secret
    return _asymmetric_key(token, settings)


# ------------------------------------------------------------------ public API
def issue_jwt(
    subject: str,
    *,
    settings,
    role: str = "user",
    tenant_id: str | None = None,
    scopes: Iterable[str] | str = (),
    ttl_seconds: int | None = None,
    jti: str | None = None,
    now: float | datetime | None = None,
) -> str:
    """Issue a short-lived JWT for subject using AUTH_SECRET.

    Only HMAC algorithms can be issued locally: asymmetric tokens must come from the identity
    provider that holds the private key. The raw token is returned and must never be logged.
    """
    clean_subject = str(subject or "").strip()
    if not clean_subject:
        raise JwtError("subject must not be empty", code="missing_subject")
    algorithm = _configured_algorithm(settings)
    if algorithm not in HMAC_ALGORITHMS:
        raise JwtError(
            f"cannot issue {algorithm} tokens locally; only HMAC algorithms are self-issued",
            code="issuing_not_supported",
        )
    secret = str(_setting(settings, "auth_secret") or "")
    if not secret:
        raise JwtError("AUTH_SECRET is not configured", code="jwt_not_configured")

    issued_at = _epoch(now)
    configured_ttl = _setting(settings, "auth_token_ttl_seconds", 3600) or 3600
    ttl = int(ttl_seconds if ttl_seconds is not None else configured_ttl)
    if ttl <= 0:
        raise JwtError("ttl_seconds must be positive", code="invalid_ttl")

    tenant = str(tenant_id or _setting(settings, "default_tenant_id", "") or "default")
    scope_values = _scope_tuple(scopes)
    claims: dict[str, Any] = {
        "sub": clean_subject,
        "role": str(role or "user"),
        "tenant_id": tenant,
        "iat": issued_at,
        "exp": issued_at + ttl,
        "jti": str(jti) if jti else uuid.uuid4().hex,
    }
    issuer = str(_setting(settings, "auth_jwt_issuer") or "").strip()
    if issuer:
        claims["iss"] = issuer
    audience = str(_setting(settings, "auth_jwt_audience") or "").strip()
    if audience:
        claims["aud"] = audience
    if scope_values:
        claims["scope"] = " ".join(scope_values)

    try:
        return str(pyjwt.encode(claims, secret, algorithm=algorithm))
    except pyjwt.PyJWTError as error:
        raise JwtError("failed to issue JWT", code="jwt_issue_failed") from error


def verify_jwt(
    token: str,
    *,
    settings,
    now: float | datetime | None = None,
    required_scopes: Iterable[str] | str = (),
) -> VerifiedToken:
    """Verify token and return the trusted identity, or raise JwtError.

    now injects a fixed clock for tests. required_scopes fails closed when the token is
    missing any listed scope.
    """
    if not isinstance(token, str) or not token.strip():
        raise JwtError("a bearer token is required", code="malformed_token")
    raw_token = token.strip()

    expected_algorithm = _configured_algorithm(settings)
    header = _unverified_header(raw_token)
    algorithm = header.get("alg")
    if not isinstance(algorithm, str) or not algorithm:
        raise JwtError("JWT header has no algorithm", code="malformed_token")
    if algorithm.lower() == "none":
        raise JwtError("unsigned JWTs (alg=none) are not accepted", code="unsigned_token")
    if algorithm.upper() != expected_algorithm:
        raise JwtError(
            f"JWT algorithm {algorithm!r} does not match the configured {expected_algorithm!r}",
            code="unsupported_algorithm",
        )

    key = _signing_key(raw_token, algorithm=expected_algorithm, settings=settings)
    issuer = str(_setting(settings, "auth_jwt_issuer") or "").strip()
    audience = str(_setting(settings, "auth_jwt_audience") or "").strip()
    options = {
        "verify_signature": True,
        "verify_exp": False,  # validated below against the injected now
        "verify_nbf": False,
        "verify_iat": False,
        "verify_aud": bool(audience),
        "verify_iss": bool(issuer),
    }
    try:
        payload = pyjwt.decode(
            raw_token,
            key,
            algorithms=[expected_algorithm],
            options=options,
            audience=audience or None,
            issuer=issuer or None,
        )
    except pyjwt.PyJWTError as error:
        raise _translate(error) from error
    if not isinstance(payload, Mapping):
        raise JwtError("malformed JWT payload", code="malformed_token")

    current = _epoch(now)
    leeway = _leeway(settings)
    expires_at = _numeric_claim(payload, "exp")
    if expires_at is None:
        raise JwtError("JWT has no expiration (exp) claim", code="missing_claim")
    if expires_at + leeway < current:
        raise JwtError("JWT has expired", code="expired_token")
    not_before = _numeric_claim(payload, "nbf")
    if not_before is not None and not_before - leeway > current:
        raise JwtError("JWT is not valid yet (nbf)", code="token_not_yet_valid")
    issued_at = _numeric_claim(payload, "iat")
    if issued_at is not None and issued_at - leeway > current:
        raise JwtError("JWT was issued in the future (iat)", code="token_not_yet_valid")

    subject = str(payload.get("sub") or "").strip()
    if not subject:
        raise JwtError("JWT has no subject (sub) claim", code="missing_subject")

    tenant_claim = payload.get("tenant_id", payload.get("tenant"))
    tenant_id = str(tenant_claim or "").strip() or str(
        _setting(settings, "default_tenant_id", "") or "default"
    )
    allowed_tenants = frozenset(getattr(settings, "allowed_tenants", ()) or ())
    if allowed_tenants and tenant_id not in allowed_tenants:
        raise JwtError(
            "JWT tenant is not in the AUTH_ALLOWED_TENANTS allowlist",
            code="tenant_not_allowed",
        )

    scopes = _merge_scopes(payload)
    required = _scope_tuple(required_scopes)
    missing = [scope for scope in required if scope not in scopes]
    if missing:
        raise JwtError(
            "JWT is missing required scope(s): " + ", ".join(missing),
            code="insufficient_scope",
        )

    jti_value = payload.get("jti")
    jti = str(jti_value).strip() if jti_value is not None else ""
    jti = jti or None
    revoked = frozenset(getattr(settings, "revoked_jtis", ()) or ())
    if jti is not None and jti in revoked:
        raise JwtError("JWT has been revoked", code="token_revoked")

    return VerifiedToken(
        subject=subject,
        issuer=str(payload.get("iss") or ""),
        audience=_audience_string(payload.get("aud"), audience),
        role=str(payload.get("role") or "user"),
        tenant_id=tenant_id,
        scopes=scopes,
        jti=jti,
        expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
        claims=dict(payload),
    )


__all__ = [
    "ASYMMETRIC_ALGORITHMS",
    "HMAC_ALGORITHMS",
    "SUPPORTED_ALGORITHMS",
    "JwtError",
    "VerifiedToken",
    "clear_jwks_cache",
    "issue_jwt",
    "verify_jwt",
]
