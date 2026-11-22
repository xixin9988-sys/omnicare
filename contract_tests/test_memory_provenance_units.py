"""Unit contract for the memory provenance attestation digest (pure, no database).

The digest is the auditable binding between a memory id, its backend and the conversation/
run that produced it. These tests pin determinism and tamper-evidence without a database or
a live memory backend:

    python -m pytest contract_tests -q
"""

from __future__ import annotations

import pytest

from app.services.attestation import attestation_digest

SECRET = "contract-test-secret"
FIELDS = {
    "memory_id": "mem-1",
    "backend": "local",
    "user_id": "u1",
    "session_id": "s1",
    "run_id": "r1",
    "source_ref": "omnicare://sessions/s1/runs/r1",
}


def _digest(secret: str = SECRET, **overrides: str) -> str:
    return attestation_digest(secret, **{**FIELDS, **overrides})


def test_digest_is_deterministic_lowercase_hex() -> None:
    first = _digest()
    assert first == _digest()
    assert len(first) == 64
    assert first == first.lower()
    assert int(first, 16) >= 0  # parses as hex


def test_different_secret_changes_digest() -> None:
    assert _digest() != _digest(secret="another-secret")
    assert _digest(secret="") != _digest()


@pytest.mark.parametrize("field", sorted(FIELDS))
def test_tampering_any_attested_field_changes_digest(field: str) -> None:
    tampered = _digest(**{field: FIELDS[field] + "-tampered"})
    assert tampered != _digest(), f"{field} must be covered by the attestation"


def test_empty_string_fields_are_attested() -> None:
    empty = _digest(source_ref="")
    assert empty != _digest()
    assert empty == _digest(source_ref="")


def test_field_boundaries_cannot_be_shifted() -> None:
    # The separator-joined payload must not let a separator smuggled into one field collide
    # with a different field split (classic concatenation/ambiguity attack).
    left = _digest(session_id="x", run_id="y")
    right = _digest(session_id="x\x1fy", run_id="")
    assert left != right
