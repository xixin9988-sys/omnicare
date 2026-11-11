"""Refund lifecycle as a pure state machine.

No database, no FastAPI, no settings: the whole refund lifecycle fits in one screen so it can
be reviewed and tested without the runtime stack. ``refund_approval`` is the only caller that
persists a transition; it must go through :func:`decide` so an illegal edge cannot be written.

States
------
``pending_review``  submitted by a customer, consumes refundable balance, no money moved yet
``approved``        an admin approved it; still consumes balance, still no money moved
``rejected``        an admin refused it; balance released
``settled``         money booked on the order (``refunded_amount``); terminal

Edges
-----
``pending_review --approve--> approved``
``pending_review --reject---> rejected``
``approved       --settle---> settled``
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class RefundStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SETTLED = "settled"


class RefundDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    SETTLE = "settle"


#: Statuses that still consume the order's refundable balance.
OPEN_STATUSES: tuple[str, ...] = (RefundStatus.PENDING_REVIEW, RefundStatus.APPROVED)

#: Statuses that can never change again.
TERMINAL_STATUSES: tuple[str, ...] = (RefundStatus.REJECTED, RefundStatus.SETTLED)

_TRANSITIONS: dict[tuple[str, str], str] = {
    (RefundStatus.PENDING_REVIEW, RefundDecision.APPROVE): RefundStatus.APPROVED,
    (RefundStatus.PENDING_REVIEW, RefundDecision.REJECT): RefundStatus.REJECTED,
    (RefundStatus.APPROVED, RefundDecision.SETTLE): RefundStatus.SETTLED,
}


class RefundTransitionError(ValueError):
    """Raised when a caller asks for an edge that does not exist (or breaks four eyes)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class RefundTransition:
    """The result of asking the machine to move one refund."""

    refund_id: str
    from_status: str
    to_status: str
    decision: str
    actor: str
    reason: str = ""

    @property
    def books_money(self) -> bool:
        return self.to_status == RefundStatus.SETTLED

    @property
    def releases_quota(self) -> bool:
        return self.to_status == RefundStatus.REJECTED


def is_open(status: str) -> bool:
    return status in OPEN_STATUSES


def allowed_decisions(status: str) -> tuple[str, ...]:
    """Decisions a caller may still attempt from ``status`` (empty when terminal)."""
    return tuple(decision for (current, decision) in _TRANSITIONS if current == status)


def decide(
    *,
    refund_id: str,
    current_status: str,
    decision: str | RefundDecision,
    actor: str,
    requester: str,
    reason: str = "",
    enforce_four_eyes: bool = True,
) -> RefundTransition:
    """Validate one transition and describe it.

    Raises:
        RefundTransitionError: unknown/terminal status, missing edge, or the requester
            trying to approve/reject/settle their own request.
    """
    decision_value = str(decision)
    if not actor:
        raise RefundTransitionError("actor_required", "A decision must record who made it.")
    if enforce_four_eyes and requester and actor == requester:
        raise RefundTransitionError(
            "requester_cannot_decide",
            "The customer who requested the refund cannot decide it (four eyes).",
        )

    target = _TRANSITIONS.get((str(current_status), decision_value))
    if target is None:
        if str(current_status) in TERMINAL_STATUSES:
            raise RefundTransitionError(
                "terminal_status",
                f"Refund is already {current_status} and cannot be changed.",
            )
        raise RefundTransitionError(
            "invalid_transition",
            f"'{current_status}' --{decision_value}--> is not an allowed refund transition.",
        )
    return RefundTransition(
        refund_id=refund_id,
        from_status=str(current_status),
        to_status=str(target),
        decision=decision_value,
        actor=actor,
        reason=reason[:500],
    )


def _normalise_amount(amount) -> str:
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError) as error:  # pragma: no cover - defensive
        raise RefundTransitionError("bad_amount", f"Not a decimal amount: {amount!r}") from error
    return f"{value.quantize(Decimal('0.01')):.2f}"


def refund_fingerprint(order_id: str, amount, reason: str = "") -> str:
    """Deterministic server-side dedupe key for a refund request.

    Callers may not supply their own key (a retry that omits it must still be idempotent), so
    the server derives one from the request's identity: same order + amount + reason collapses
    to the same open request. ``reason`` is whitespace-normalised so trailing spaces do not
    create a second request.
    """
    normalised_reason = " ".join(str(reason or "").split())
    material = f"{order_id}|{_normalise_amount(amount)}|{normalised_reason}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


__all__ = [
    "OPEN_STATUSES",
    "TERMINAL_STATUSES",
    "RefundDecision",
    "RefundStatus",
    "RefundTransition",
    "RefundTransitionError",
    "allowed_decisions",
    "decide",
    "is_open",
    "refund_fingerprint",
]
