"""Authoritative refund math shared by the governance guard and the tool.

The available amount is ``total - refunded - open_requests`` where ``open_requests`` are the
requests that have not been rejected or completed. Both call sites must use this helper; a
guard that checks a different number than the tool is a security bug, not a style issue.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from app.models.entities import RefundRequest

OPEN_REFUND_STATUSES = ("pending_review", "approved")


async def open_refund_total(db: Any, order_id: str) -> Decimal:
    """Sum of refund requests that still consume the order's refundable balance."""
    total = await db.scalar(
        select(func.coalesce(func.sum(RefundRequest.amount), 0)).where(
            RefundRequest.order_id == order_id,
            RefundRequest.status.in_(OPEN_REFUND_STATUSES),
        )
    )
    return Decimal(total or 0)


async def available_refund(db: Any, order: Any) -> Decimal:
    """Authoritative refundable balance, including pending/approved requests."""
    total = Decimal(str(order.total_amount))
    refunded = Decimal(str(order.refunded_amount))
    pending = await open_refund_total(db, order.id)
    return max(Decimal(0), total - refunded - pending)


__all__ = ["OPEN_REFUND_STATUSES", "available_refund", "open_refund_total"]
