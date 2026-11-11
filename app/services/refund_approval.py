"""Persistence layer for the refund approval state machine.

``refund_state`` decides *whether* a transition is legal; this module writes it: it locks the
request (and the order, where the backend supports row locks), re-checks the authoritative
balance, applies the transition and appends an audit row. Routes only translate errors.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import desc, select

from app.models.entities import Order, RefundAudit, RefundRequest, now_utc
from app.services.refund_state import (
    RefundTransition,
    RefundTransitionError,
    decide,
)
from app.services.refunds import available_refund, supports_row_lock


class RefundApprovalError(Exception):
    """Domain failure; ``code`` is stable enough for API clients to branch on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _locked(stmt, db):
    return stmt.with_for_update() if supports_row_lock(db) else stmt


async def get_refund(db, refund_id: str) -> RefundRequest | None:
    return await db.scalar(select(RefundRequest).where(RefundRequest.id == refund_id))


async def list_refunds(
    db, *, status: str | None = None, user_id: str | None = None, limit: int = 100
) -> list[RefundRequest]:
    stmt = select(RefundRequest)
    if status:
        stmt = stmt.where(RefundRequest.status == status)
    if user_id:
        stmt = stmt.where(RefundRequest.user_id == user_id)
    stmt = stmt.order_by(desc(RefundRequest.created_at)).limit(max(1, min(limit, 500)))
    return list((await db.scalars(stmt)).all())


async def decide_refund(
    db,
    *,
    refund_id: str,
    decision: str,
    actor: str,
    reason: str = "",
) -> tuple[RefundRequest, RefundTransition]:
    """Apply ``decision`` to one refund, or raise :class:`RefundApprovalError`.

    The caller owns the transaction: this function flushes but never commits, so a route can
    commit the status change and the audit row atomically.
    """
    refund = await db.scalar(
        _locked(select(RefundRequest).where(RefundRequest.id == refund_id), db)
    )
    if refund is None:
        raise RefundApprovalError("refund_not_found", "Refund request not found.")

    try:
        transition = decide(
            refund_id=refund.id,
            current_status=refund.status,
            decision=decision,
            actor=actor,
            requester=refund.user_id,
            reason=reason,
        )
    except RefundTransitionError as error:
        raise RefundApprovalError(error.code, error.message) from error

    order = await db.scalar(_locked(select(Order).where(Order.id == refund.order_id), db))
    if order is None:
        raise RefundApprovalError("order_not_found", "The order behind this refund is gone.")

    # Approval reserves money, so the balance must still cover it once it is decided. The
    # request being decided is excluded from the open total, otherwise it would count twice.
    if transition.to_status == "approved":
        available = await available_refund(db, order, exclude_refund_id=refund.id)
        if Decimal(str(refund.amount)) > available:
            raise RefundApprovalError(
                "refund_exceeds_authoritative_limit",
                f"Only {available:.2f} is refundable after concurrent requests.",
            )

    if transition.books_money:
        order.refunded_amount = Decimal(str(order.refunded_amount)) + Decimal(str(refund.amount))
        order.status = "refunded"

    refund.status = transition.to_status
    refund.decided_by = actor
    refund.decided_at = now_utc()
    refund.decision_reason = transition.reason

    db.add(
        RefundAudit(
            refund_id=refund.id,
            order_id=refund.order_id,
            from_status=transition.from_status,
            to_status=transition.to_status,
            decision=transition.decision,
            actor=actor,
            reason=transition.reason,
        )
    )
    await db.flush()
    return refund, transition


async def audit_trail(db, refund_id: str) -> list[RefundAudit]:
    rows = await db.scalars(
        select(RefundAudit)
        .where(RefundAudit.refund_id == refund_id)
        .order_by(RefundAudit.created_at)
    )
    return list(rows.all())


__all__ = [
    "RefundApprovalError",
    "audit_trail",
    "decide_refund",
    "get_refund",
    "list_refunds",
]
