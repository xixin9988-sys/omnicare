"""Refund state machine: pure transitions, runnable without the database stack (R-03 / P0-1)."""

from __future__ import annotations

import pytest

from app.services.refund_state import (
    OPEN_STATUSES,
    TERMINAL_STATUSES,
    RefundDecision,
    RefundStatus,
    RefundTransitionError,
    allowed_decisions,
    decide,
    is_open,
    refund_fingerprint,
)

BASE = {
    "refund_id": "rf-1",
    "requester": "alice",
    "actor": "admin-1",
    "reason": "customer asked",
}


def test_pending_can_be_approved() -> None:
    transition = decide(
        current_status=RefundStatus.PENDING_REVIEW, decision=RefundDecision.APPROVE, **BASE
    )
    assert transition.from_status == "pending_review"
    assert transition.to_status == "approved"
    assert transition.decision == "approve"
    assert transition.actor == "admin-1"
    assert transition.books_money is False
    assert transition.releases_quota is False


def test_pending_can_be_rejected_and_releases_quota() -> None:
    transition = decide(current_status="pending_review", decision="reject", **BASE)
    assert transition.to_status == "rejected"
    assert transition.releases_quota is True
    assert transition.books_money is False


def test_only_approved_can_be_settled() -> None:
    transition = decide(current_status="approved", decision="settle", **BASE)
    assert transition.to_status == "settled"
    assert transition.books_money is True


@pytest.mark.parametrize(
    ("status", "decision"),
    [
        ("pending_review", "settle"),
        ("approved", "approve"),
        ("approved", "reject"),
        ("rejected", "approve"),
        ("rejected", "settle"),
        ("settled", "settle"),
    ],
)
def test_illegal_edges_are_refused(status: str, decision: str) -> None:
    with pytest.raises(RefundTransitionError) as error:
        decide(current_status=status, decision=decision, **BASE)
    assert error.value.code in {"invalid_transition", "terminal_status"}


def test_terminal_status_is_reported_as_such() -> None:
    with pytest.raises(RefundTransitionError) as error:
        decide(current_status="settled", decision="approve", **BASE)
    assert error.value.code == "terminal_status"


def test_requester_cannot_decide_own_refund() -> None:
    with pytest.raises(RefundTransitionError) as error:
        decide(current_status="pending_review", decision="approve", **dict(BASE, actor="alice"))
    assert error.value.code == "requester_cannot_decide"


def test_four_eyes_can_be_disabled_explicitly() -> None:
    transition = decide(
        current_status="pending_review",
        decision="approve",
        **dict(BASE, actor="alice"),
        enforce_four_eyes=False,
    )
    assert transition.to_status == "approved"


def test_decision_must_record_an_actor() -> None:
    with pytest.raises(RefundTransitionError) as error:
        decide(current_status="pending_review", decision="approve", **dict(BASE, actor=""))
    assert error.value.code == "actor_required"


def test_long_reason_is_truncated_to_the_column_size() -> None:
    transition = decide(
        current_status="pending_review", decision="reject", **dict(BASE, reason="x" * 900)
    )
    assert len(transition.reason) == 500


def test_allowed_decisions_and_openness() -> None:
    assert set(allowed_decisions("pending_review")) == {"approve", "reject"}
    assert allowed_decisions("approved") == ("settle",)
    assert allowed_decisions("rejected") == ()
    assert is_open("pending_review") and is_open("approved")
    assert not is_open("rejected") and not is_open("settled")
    assert "rejected" in TERMINAL_STATUSES and "settled" in TERMINAL_STATUSES
    assert set(OPEN_STATUSES) == {"pending_review", "approved"}


def test_fingerprint_is_stable_and_amount_aware() -> None:
    first = refund_fingerprint("ORD-1", "40.00", "user asked")
    assert first == refund_fingerprint("ORD-1", 40, "user asked")
    assert first == refund_fingerprint("ORD-1", "40.0", "  user   asked ")
    assert len(first) == 32
    assert first != refund_fingerprint("ORD-1", "40.01", "user asked")
    assert first != refund_fingerprint("ORD-2", "40.00", "user asked")
    assert first != refund_fingerprint("ORD-1", "40.00", "different reason")
