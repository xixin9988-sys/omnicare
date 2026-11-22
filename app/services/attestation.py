"""Pure provenance-attestation primitives (no database imports).

The digest binds one memory record to the conversation/run that produced it. It lives in its
own module so contract tests can pin determinism and tamper-evidence without the SQLAlchemy
stack; the persistence layer (app.services.memory_provenance) re-exports these names.

The HMAC key never leaves the process and is never written into a memory, event payload or
API response.
"""

from __future__ import annotations

import hashlib
import hmac

ISSUER = "omnicare"
# Documented fallback so a local demo without AUTH_SECRET still gets a stable HMAC key.
# It provides tamper detection, not authentication: production deployments set AUTH_SECRET.
FALLBACK_SECRET = "omnicare-local-provenance"

_ATTESTATION_VERSION = "omnicare-memory-provenance-v1"
_FIELD_SEPARATOR = "\x1f"

REASON_MISSING = "memory_provenance_missing"
REASON_INTEGRITY = "memory_provenance_integrity_mismatch"
REASON_SOURCE = "memory_provenance_source_mismatch"
REASON_VERIFIED = "memory_provenance_verified"


def resolve_attestation_secret(settings: object) -> str:
    """Return the HMAC key: AUTH_SECRET when configured, else the local fallback."""
    return str(getattr(settings, "auth_secret", "") or FALLBACK_SECRET)


def attestation_digest(
    secret: str,
    *,
    memory_id: str,
    backend: str,
    user_id: str,
    session_id: str,
    run_id: str,
    source_ref: str,
) -> str:
    """Canonical HMAC-SHA256 hex digest over the attested provenance fields.

    Fields are separator-joined (not JSON) so the digest is stable across Python/JSON
    versions; the unit separator cannot appear in ids, refs or backend names.
    """
    payload = _FIELD_SEPARATOR.join(
        (
            _ATTESTATION_VERSION,
            str(memory_id),
            str(backend),
            str(user_id),
            str(session_id),
            str(run_id),
            str(source_ref),
        )
    )
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


__all__ = [
    "FALLBACK_SECRET",
    "ISSUER",
    "REASON_INTEGRITY",
    "REASON_MISSING",
    "REASON_SOURCE",
    "REASON_VERIFIED",
    "attestation_digest",
    "resolve_attestation_secret",
]
