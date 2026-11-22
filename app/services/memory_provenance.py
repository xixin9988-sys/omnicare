"""HMAC-attested provenance for memories written by OmniCare.

The attestation row is the persistent audit record; the digest binds it to the memory id,
the backend and the conversation/run that produced it. At retrieval time a memory is
trusted only when its row exists, the digest matches, and the memory's own provenance
points at the recorded source_ref. The HMAC key never leaves the process and is never
written into a memory, event payload or API response.

The pure digest primitives live in :mod:`app.services.attestation` so contract tests can run
without the SQLAlchemy stack; this module adds the persistence half.
"""

from __future__ import annotations

import hmac

from app.memory.ports import MemoryRecord
from app.models.entities import MemoryProvenance
from app.services.attestation import (
    FALLBACK_SECRET,
    ISSUER,
    REASON_INTEGRITY,
    REASON_MISSING,
    REASON_SOURCE,
    REASON_VERIFIED,
    attestation_digest,
    resolve_attestation_secret,
)


def _memory_provenance(memory: object) -> dict[str, str]:
    provenance = getattr(memory, "provenance", None)
    if not isinstance(provenance, dict):
        return {}
    return {str(key): value for key, value in provenance.items() if isinstance(value, str)}


async def record_memory_provenance(
    db,
    *,
    memory: MemoryRecord,
    user_id: str,
    session_id: str,
    run_id: str,
    tenant_id: str = "default",
    backend: str,
    secret: str,
) -> MemoryProvenance:
    """Add (never commit) the attestation row for one just-written memory.

    The caller owns the transaction so the memory and its attestation become visible
    atomically: a preference memory without a matching row is treated as untrusted later.
    """
    provenance = _memory_provenance(memory)
    source_ref = str(provenance.get("source_ref") or "")
    row = MemoryProvenance(
        memory_id=str(memory.id),
        backend=backend,
        tenant_id=tenant_id or "default",
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        source_type=str(provenance.get("source_kind") or "user"),
        source_ref=source_ref,
        created_by=str(provenance.get("created_by") or "omnicare"),
        issuer=ISSUER,
        integrity=attestation_digest(
            secret,
            memory_id=str(memory.id),
            backend=backend,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            source_ref=source_ref,
        ),
    )
    db.add(row)
    return row


async def get_memory_provenance(db, memory_id: str) -> MemoryProvenance | None:
    return await db.get(MemoryProvenance, memory_id)


def verify_provenance_row(*, row: MemoryProvenance | None, secret: str) -> tuple[bool, str]:
    """Verify existence and digest of an attestation row (no memory bytes needed)."""
    if row is None:
        return False, REASON_MISSING
    expected = attestation_digest(
        secret,
        memory_id=row.memory_id,
        backend=row.backend,
        user_id=row.user_id,
        session_id=row.session_id,
        run_id=row.run_id,
        source_ref=row.source_ref or "",
    )
    if not hmac.compare_digest(str(row.integrity or ""), expected):
        return False, REASON_INTEGRITY
    return True, REASON_VERIFIED


async def verify_memory_provenance(db, *, memory: MemoryRecord, secret: str) -> tuple[bool, str]:
    """Check that a retrieved memory has a matching, untampered attestation row.

    Returns (ok, reason). Reasons are stable strings the supervisor records in the
    governance incident detail, while the incident reason_code stays the generic
    memory_provenance_unverified.
    """
    row = await get_memory_provenance(db, str(getattr(memory, "id", "")))
    ok, reason = verify_provenance_row(row=row, secret=secret)
    if not ok:
        return False, reason
    memory_ref = str(_memory_provenance(memory).get("source_ref") or "")
    if memory_ref != str(row.source_ref or ""):
        return False, REASON_SOURCE
    return True, REASON_VERIFIED


def serialize_memory_provenance(row: MemoryProvenance, *, verified: bool) -> dict:
    """API projection of one attestation row (the digest itself is not exposed)."""
    return {
        "id": row.memory_id,
        "source_ref": row.source_ref,
        "created_by": row.created_by,
        "tenant_id": row.tenant_id,
        "backend": row.backend,
        "session_id": row.session_id,
        "run_id": row.run_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "verified": verified,
    }


__all__ = [
    "FALLBACK_SECRET",
    "ISSUER",
    "REASON_INTEGRITY",
    "REASON_MISSING",
    "REASON_SOURCE",
    "REASON_VERIFIED",
    "attestation_digest",
    "get_memory_provenance",
    "record_memory_provenance",
    "resolve_attestation_secret",
    "serialize_memory_provenance",
    "verify_memory_provenance",
    "verify_provenance_row",
]
