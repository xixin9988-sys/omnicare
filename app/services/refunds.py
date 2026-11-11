"""Authoritative refund math shared by the governance guard and the tool.

The available amount is ``total - refunded - open_requests`` where ``open_requests`` are the
requests that have not been rejected or settled. Both call sites must use this helper; a
guard that checks a different number than the tool is a security bug, not a style issue.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from app.models.entities import RefundRequest
from app.services.refund_state import OPEN_STATUSES, refund_fingerprint

# Kept as a module constant for callers that predate ``refund_state``; single source above.
OPEN_REFUND_STATUSES = OPEN_STATUSES


def supports_row_lock(db: Any) -> bool:
    """SQLite ignores/forbids FOR UPDATE; PostgreSQL and MySQL support it."""
    bind = getattr(db, "bind", None)
    dialect = getattr(getattr(bind, "dialect", None), "name", "")
    return bool(dialect) and dialect not in {"sqlite"}


def lock_clause(db: Any) -> dict[str, bool]:
    """``select(...).with_for_update(**lock_clause(db))`` is a no-op on SQLite."""
    return {"nowait": False} if supports_row_lock(db) else {}


async def open_refund_total(
    db: Any, order_id: str, *, exclude_refund_id: str | None = None
) -> Decimal:
    """Sum of refund requests that still consume the order's refundable balance.

    ``exclude_refund_id`` lets a decision re-check the balance while ignoring the request it
    is deciding (that request is already counted in the sum).
    """
    stmt = select(func.coalesce(func.sum(RefundRequest.amount), 0)).where(
        RefundRequest.order_id == order_id,
        RefundRequest.status.in_(OPEN_REFUND_STATUSES),
    )
    if exclude_refund_id:
        stmt = stmt.where(RefundRequest.id != exclude_refund_id)
    total = await db.scalar(stmt)
    return Decimal(total or 0)


async def available_refund(db: Any, order: Any, *, exclude_refund_id: str | None = None) -> Decimal:
    """Authoritative refundable balance, including pending/approved requests."""
    total = Decimal(str(order.total_amount))
    refunded = Decimal(str(order.refunded_amount))
    pending = await open_refund_total(db, order.id, exclude_refund_id=exclude_refund_id)
    return max(Decimal(0), total - refunded - pending)


__all__ = [
    "OPEN_REFUND_STATUSES",
    "available_refund",
    "lock_clause",
    "open_refund_total",
    "refund_fingerprint",
    "supports_row_lock",
]
