"""PostgreSQL concurrency (R-04).

SQLite has no row locks, so these tests only run when ``DATABASE_URL`` points at PostgreSQL —
which the CI ``full-suite`` job does. Without ``SELECT ... FOR UPDATE`` both sessions would read
the same balance (or the same refund status) before either writes.
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.entities import Order, RefundRequest, User
from app.services.refund_approval import RefundApprovalError, decide_refund
from app.tools.ecommerce import RefundArgs, request_refund
from app.tools.registry import ToolContext

DATABASE_URL = os.environ.get("DATABASE_URL") or get_settings().database_url
IS_POSTGRES = DATABASE_URL.startswith("postgresql")

pytestmark = pytest.mark.skipif(not IS_POSTGRES, reason="requires PostgreSQL FOR UPDATE row locks")


async def seed_order(total: str = "100.00") -> None:
    async with SessionLocal() as db:
        db.add(User(id="alice", display_name="Alice"))
        await db.flush()
        db.add(
            Order(
                id="ORD-1",
                user_id="alice",
                total_amount=Decimal(total),
                refunded_amount=Decimal("0.00"),
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_two_concurrent_requests_cannot_both_pass_the_balance_check() -> None:
    await seed_order("100.00")

    async def submit() -> str:
        async with SessionLocal() as db:
            context = ToolContext(
                user_id="alice", session_id="s1", run_id="r1", db=db, governance=None
            )
            result = await request_refund(
                RefundArgs(order_id="ORD-1", amount=Decimal("60.00")), context
            )
            await db.commit()
            return "created" if result.success else str(result.error_code)

    results = await asyncio.gather(submit(), submit())

    assert sorted(results) == ["created", "refund_exceeds_authoritative_limit"]
    async with SessionLocal() as db:
        rows = list((await db.scalars(select(RefundRequest))).all())
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_two_admins_cannot_approve_the_same_refund() -> None:
    await seed_order("100.00")
    async with SessionLocal() as db:
        refund = RefundRequest(
            order_id="ORD-1", user_id="alice", amount=Decimal("40.00"), status="pending_review"
        )
        db.add(refund)
        await db.commit()
        refund_id = refund.id

    async def approve(actor: str) -> str:
        async with SessionLocal() as db:
            try:
                await decide_refund(db, refund_id=refund_id, decision="approve", actor=actor)
                await db.commit()
                return "approved"
            except RefundApprovalError as error:
                await db.rollback()
                return error.code

    results = await asyncio.gather(approve("admin-1"), approve("admin-2"))

    assert sorted(results) == ["approved", "invalid_transition"]
    async with SessionLocal() as db:
        current = await db.get(RefundRequest, refund_id)
        assert current.status == "approved"
