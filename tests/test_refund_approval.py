"""Refund approval state machine, persisted (needs SQLAlchemy; runs in CI on SQLite + PG)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import Principal, require_admin
from app.core.db import SessionLocal
from app.main import app
from app.models.entities import Order, RefundRequest, User
from app.services.refund_approval import RefundApprovalError, audit_trail, decide_refund
from app.services.refunds import available_refund


async def seed(db, *, total: str = "100.00", refunded: str = "0.00") -> None:
    db.add(User(id="alice", display_name="Alice"))
    db.add(User(id="admin-1", display_name="Admin"))
    await db.flush()  # parents before children: PostgreSQL enforces the FK, SQLite does not
    db.add(
        Order(
            id="ORD-1",
            user_id="alice",
            total_amount=Decimal(total),
            refunded_amount=Decimal(refunded),
        )
    )
    await db.flush()


async def make_refund(
    db, *, amount: str = "40.00", status: str = "pending_review"
) -> RefundRequest:
    refund = RefundRequest(
        order_id="ORD-1",
        user_id="alice",
        amount=Decimal(amount),
        status=status,
        reason="size does not fit",
    )
    db.add(refund)
    await db.flush()
    return refund


@pytest.mark.asyncio
async def test_approve_reserves_balance_and_audits() -> None:
    async with SessionLocal() as db:
        await seed(db)
        refund = await make_refund(db, amount="40.00")

        decided, transition = await decide_refund(
            db, refund_id=refund.id, decision="approve", actor="admin-1", reason="ok"
        )
        await db.commit()

        assert decided.status == "approved"
        assert transition.from_status == "pending_review"
        assert decided.decided_by == "admin-1"
        assert decided.decided_at is not None

        order = await db.get(Order, "ORD-1")
        # Approval reserves money but does not book it yet.
        assert Decimal(str(order.refunded_amount)) == Decimal("0.00")
        assert await available_refund(db, order) == Decimal("60.00")

        trail = await audit_trail(db, refund.id)
        assert len(trail) == 1
        assert (trail[0].from_status, trail[0].to_status) == ("pending_review", "approved")
        assert trail[0].actor == "admin-1"


@pytest.mark.asyncio
async def test_reject_releases_quota() -> None:
    async with SessionLocal() as db:
        await seed(db)
        refund = await make_refund(db, amount="40.00")
        order = await db.get(Order, "ORD-1")
        assert await available_refund(db, order) == Decimal("60.00")

        await decide_refund(db, refund_id=refund.id, decision="reject", actor="admin-1")
        await db.commit()
        await db.refresh(refund)

        assert refund.status == "rejected"
        assert await available_refund(db, order) == Decimal("100.00")


@pytest.mark.asyncio
async def test_settle_books_the_money_once() -> None:
    async with SessionLocal() as db:
        await seed(db)
        refund = await make_refund(db, amount="40.00")
        await decide_refund(db, refund_id=refund.id, decision="approve", actor="admin-1")
        await db.commit()

        settled, transition = await decide_refund(
            db, refund_id=refund.id, decision="settle", actor="admin-1"
        )
        await db.commit()
        await db.refresh(settled)

        order = await db.get(Order, "ORD-1")
        assert settled.status == "settled"
        assert transition.books_money
        assert Decimal(str(order.refunded_amount)) == Decimal("40.00")
        assert order.status == "refunded"
        # Settled rows leave the open set, so the balance is not double counted.
        assert await available_refund(db, order) == Decimal("60.00")
        assert len(await audit_trail(db, refund.id)) == 2


@pytest.mark.asyncio
async def test_requester_cannot_decide_own_refund() -> None:
    async with SessionLocal() as db:
        await seed(db)
        refund = await make_refund(db)
        with pytest.raises(RefundApprovalError) as error:
            await decide_refund(
                db, refund_id=refund.id, decision="approve", actor="alice", reason="self serve"
            )
        assert error.value.code == "requester_cannot_decide"


@pytest.mark.asyncio
async def test_deciding_twice_is_refused() -> None:
    async with SessionLocal() as db:
        await seed(db)
        refund = await make_refund(db)
        await decide_refund(db, refund_id=refund.id, decision="approve", actor="admin-1")
        await db.commit()
        with pytest.raises(RefundApprovalError) as error:
            await decide_refund(db, refund_id=refund.id, decision="approve", actor="admin-2")
        assert error.value.code == "invalid_transition"


@pytest.mark.asyncio
async def test_approval_cannot_exceed_the_authoritative_balance() -> None:
    """A request created under a stale quote must not be approvable after another one wins."""
    async with SessionLocal() as db:
        await seed(db, total="100.00")
        first = await make_refund(db, amount="60.00")
        await decide_refund(db, refund_id=first.id, decision="approve", actor="admin-1")
        await db.commit()

        # Created before the first approval was visible (a quote the customer already saw).
        stale = await make_refund(db, amount="60.00")
        await db.commit()

        with pytest.raises(RefundApprovalError) as error:
            await decide_refund(db, refund_id=stale.id, decision="approve", actor="admin-1")
        assert error.value.code == "refund_exceeds_authoritative_limit"
        await db.rollback()
        await db.refresh(stale)
        assert stale.status == "pending_review"


@pytest.mark.asyncio
async def test_unknown_refund_is_a_clean_error() -> None:
    async with SessionLocal() as db:
        with pytest.raises(RefundApprovalError) as error:
            await decide_refund(db, refund_id="missing", decision="approve", actor="admin-1")
        assert error.value.code == "refund_not_found"


@pytest.mark.asyncio
async def test_api_rejects_non_admin_decision() -> None:
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post(
            "/api/v1/refunds/whatever/decision",
            json={"decision": "approve"},
            headers={"X-User-Id": "demo_user_001"},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "admin_required"


@pytest.mark.asyncio
async def test_admin_can_decide_and_read_the_audit_trail() -> None:
    async with SessionLocal() as db:
        await seed(db)
        refund = await make_refund(db, amount="40.00")
        await db.commit()
        refund_id = refund.id

    app.dependency_overrides[require_admin] = lambda: Principal(user_id="admin-1", role="admin")
    try:
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            response = await client.post(
                f"/api/v1/refunds/{refund_id}/decision",
                json={"decision": "approve", "reason": "checked in CRM"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "approved"

            audit = await client.get(f"/api/v1/refunds/{refund_id}/audit")
            assert audit.status_code == 200
            assert audit.json()[0]["to_status"] == "approved"
            assert audit.json()[0]["actor"] == "admin-1"
    finally:
        app.dependency_overrides.pop(require_admin, None)
