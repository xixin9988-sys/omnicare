"""The 60-second demo scenario: high-value policy rule, deterministic flow, signed replay."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.governance.local_guard import LocalPolicyGuard
from app.main import app
from app.models.entities import (
    AgentEvent,
    GovernanceIncident,
    Order,
    RefundAudit,
    RefundRequest,
    User,
)

DEMO_USER = "demo_user_001"
DEMO_ORDER = "ORD-001"
STEP_KEYS = ("logistics", "refund", "escalate", "approve")


async def _seed_demo_order(db) -> None:
    db.add(User(id=DEMO_USER, display_name="Demo User"))
    await db.flush()  # parents before children: PostgreSQL enforces the FK, SQLite does not
    db.add(
        Order(
            id=DEMO_ORDER,
            user_id=DEMO_USER,
            status="shipped",
            carrier="顺丰速运",
            tracking_no="SF-ORD-001",
            total_amount=Decimal("120000.00"),
            refunded_amount=Decimal("0.00"),
        )
    )
    await db.flush()


async def _start_run(client) -> str:
    response = await client.post("/api/v1/demo/runs")
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


async def _run_step(client, run_id: str, key: str) -> dict:
    response = await client.post(f"/api/v1/demo/runs/{run_id}/steps/{key}")
    assert response.status_code == 200, response.text
    return response.json()


async def _run_full_flow(client) -> tuple[str, dict[str, dict]]:
    run_id = await _start_run(client)
    bodies = {}
    for key in STEP_KEYS:
        bodies[key] = await _run_step(client, run_id, key)
    return run_id, bodies


# ---------------------------------------------------------------- policy unit tests
@pytest.mark.asyncio
async def test_high_value_refund_is_quarantined_by_named_rule() -> None:
    async with SessionLocal() as db:
        await _seed_demo_order(db)
        guard = LocalPolicyGuard("omnicare-demo-1", Decimal("10000.00"))

        decision = await guard.authorize_action(
            action="request_refund",
            args={"order_id": DEMO_ORDER, "amount": "100000.00"},
            context={"user_id": DEMO_USER, "session_id": "s-1"},
            db=db,
        )

        assert decision.allowed is False
        assert decision.decision == "quarantine"
        assert decision.reason_code == "high_value_refund_requires_human"
        assert decision.rule_id == "high-value-refund"
        assert decision.rule_label == "高额退款必须人工审批"
        assert decision.matched_rules == ("high-value-refund",)
        assert "100000.00" in decision.detail


@pytest.mark.asyncio
async def test_human_escalation_and_small_amounts_bypass_the_high_value_rule() -> None:
    async with SessionLocal() as db:
        await _seed_demo_order(db)
        guard = LocalPolicyGuard("omnicare-demo-1", Decimal("10000.00"))

        escalated = await guard.authorize_action(
            action="request_refund",
            args={"order_id": DEMO_ORDER, "amount": "100000.00"},
            context={"user_id": DEMO_USER, "session_id": "s-1", "human_escalated": True},
            db=db,
        )
        assert escalated.allowed is True
        assert escalated.reason_code == "refund_within_authoritative_limit"

        small = await guard.authorize_action(
            action="request_refund",
            args={"order_id": DEMO_ORDER, "amount": "100.00"},
            context={"user_id": DEMO_USER, "session_id": "s-1"},
            db=db,
        )
        assert small.allowed is True


@pytest.mark.asyncio
async def test_guard_without_threshold_keeps_legacy_behavior() -> None:
    async with SessionLocal() as db:
        await _seed_demo_order(db)
        guard = LocalPolicyGuard("omnicare-demo-1")
        decision = await guard.authorize_action(
            action="request_refund",
            args={"order_id": DEMO_ORDER, "amount": "100000.00"},
            context={"user_id": DEMO_USER, "session_id": "s-1"},
            db=db,
        )
        assert decision.allowed is True


# ---------------------------------------------------------------- API flow
@pytest.mark.asyncio
async def test_manifest_describes_the_fixed_scenario(client) -> None:
    response = await client.get("/api/v1/demo/manifest")
    assert response.status_code == 200, response.text
    manifest = response.json()

    assert manifest["title"]
    assert manifest["order"] == {
        "id": "ORD-001",
        "total_amount": "120000.00",
        "status": "shipped",
        "carrier": "顺丰速运",
        "tracking_no": "SF-ORD-001",
    }
    assert manifest["amount"] == "100000.00"
    assert manifest["duration_seconds"] == 60
    assert [step["key"] for step in manifest["steps"]] == [
        "logistics",
        "refund",
        "escalate",
        "approve",
    ]
    assert [step["index"] for step in manifest["steps"]] == [1, 2, 3, 4]
    assert [step["label"] for step in manifest["steps"]] == [
        "查物流",
        "申请退款",
        "转人工",
        "批准放行",
    ]
    assert manifest["mcp"]["transport"] == "streamable-http"
    assert manifest["mcp"]["endpoint"] == "/mcp"
    assert [tool["name"] for tool in manifest["mcp"]["tools"]] == [
        "check_logistics",
        "request_refund",
    ]
    assert manifest["mcp"]["tools"][0]["read_only"] is True
    assert manifest["mcp"]["tools"][1]["read_only"] is False


@pytest.mark.asyncio
async def test_unknown_run_and_disabled_demo_are_clean_errors(client) -> None:
    missing = await client.get("/api/v1/demo/runs/does-not-exist/replay")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "run_not_found"

    settings = app.state.settings
    original_env, original_mode = settings.app_env, settings.demo_mode
    try:
        settings.app_env = "production"
        settings.demo_mode = False
        disabled = await client.get("/api/v1/demo/manifest")
        assert disabled.status_code == 404
        assert disabled.json()["detail"]["code"] == "demo_disabled"
    finally:
        settings.app_env = original_env
        settings.demo_mode = original_mode


@pytest.mark.asyncio
async def test_full_scenario_signs_every_step_and_replays_verified(client) -> None:
    run_id = await _start_run(client)

    # ---- step 1: logistics (real read-only tool, signed)
    logistics = await _run_step(client, run_id, "logistics")
    assert logistics["step"] == 1
    assert logistics["status"] == "ok"
    assert logistics["data"] == {
        "carrier": "顺丰速运",
        "tracking_no": "SF-ORD-001",
        "status": "运输中，预计明日送达",
    }
    assert logistics["policy"] is None
    assert logistics["signature"]["alg"] == "HMAC-SHA256"
    assert len(logistics["signature"]["value"]) == 64
    assert logistics["signature"]["short"] == logistics["signature"]["value"][:12]
    provenance = logistics["provenance"]
    assert provenance["source_ref"] == f"omnicare://demo/runs/{run_id}/steps/1"
    assert provenance["event_id"] == logistics["event"]["id"]
    assert provenance["order_id"] == "ORD-001"
    assert provenance["tool"] == "get_logistics"
    assert provenance["policy_version"]

    # ---- step 2: refund is blocked by the named high-value rule, and creates no request
    refund = await _run_step(client, run_id, "refund")
    assert refund["step"] == 2
    assert refund["status"] == "blocked"
    assert refund["title"] == "策略拦截"
    assert refund["data"] == {
        "amount": "100000.00",
        "order_id": "ORD-001",
        "decision": "quarantine",
    }
    policy = refund["policy"]
    assert policy["rule_id"] == "high-value-refund"
    assert policy["rule_label"] == "高额退款必须人工审批"
    assert policy["reason_code"] == "high_value_refund_requires_human"
    assert policy["threshold"] == "10000.00"
    assert policy["matched_rules"] == ["high-value-refund"]
    assert policy["detail"]
    assert refund["provenance"]["tool"] == "request_refund"

    async with SessionLocal() as db:
        refunds = await db.scalar(
            select(func.count(RefundRequest.id)).where(RefundRequest.order_id == DEMO_ORDER)
        )
        audits = await db.scalar(
            select(func.count(RefundAudit.id)).where(RefundAudit.order_id == DEMO_ORDER)
        )
        incidents = await db.scalar(
            select(func.count(GovernanceIncident.id)).where(
                GovernanceIncident.reason_code == "high_value_refund_requires_human"
            )
        )
        denied = await db.scalar(
            select(func.count(AgentEvent.id)).where(
                AgentEvent.run_id == run_id, AgentEvent.event_type == "tool_denied"
            )
        )
    assert refunds == 0, "the blocked step must not create a refund request"
    assert audits == 0
    assert incidents == 1, "the denial must be recorded in the existing governance incident log"
    assert denied == 1

    # ---- step 3: escalation creates the real pending_review request through the tool
    escalate = await _run_step(client, run_id, "escalate")
    refund_id = escalate["refund"]["id"]
    assert escalate["step"] == 3
    assert escalate["status"] == "pending_review"
    assert escalate["data"] == {
        "refund_id": refund_id,
        "amount": "100000.00",
        "status": "pending_review",
    }
    assert escalate["refund"] == {
        "id": refund_id,
        "status": "pending_review",
        "amount": "100000.00",
        "decided_by": None,
    }
    assert escalate["policy"] is None
    assert escalate["provenance"]["refund_id"] == refund_id

    # ---- step 4: approval through refund_approval, with the audit row
    approve = await _run_step(client, run_id, "approve")
    assert approve["step"] == 4
    assert approve["status"] == "approved"
    assert approve["title"] == "人工审批通过 · 放行"
    assert approve["data"] == {
        "refund_id": refund_id,
        "amount": "100000.00",
        "status": "approved",
        "decided_by": "demo_operator",
    }
    assert approve["refund"]["status"] == "approved"
    assert approve["audit"]["from_status"] == "pending_review"
    assert approve["audit"]["to_status"] == "approved"
    assert approve["audit"]["decision"] == "approve"
    assert approve["audit"]["actor"] == "demo_operator"
    assert approve["provenance"]["refund_id"] == refund_id

    async with SessionLocal() as db:
        stored = await db.get(RefundRequest, refund_id)
        assert stored is not None and stored.status == "approved"
        trail = (
            await db.scalars(select(RefundAudit).where(RefundAudit.refund_id == refund_id))
        ).all()
        assert len(trail) == 1

    # ---- replay: every persisted signature verifies
    replay = await client.get(f"/api/v1/demo/runs/{run_id}/replay")
    assert replay.status_code == 200, replay.text
    timeline = replay.json()
    assert timeline["run_id"] == run_id
    assert timeline["verified"] is True
    assert [step["index"] for step in timeline["steps"]] == [1, 2, 3, 4]
    assert [step["key"] for step in timeline["steps"]] == list(STEP_KEYS)
    assert [step["label"] for step in timeline["steps"]] == [
        "查物流",
        "申请退款",
        "转人工",
        "批准放行",
    ]
    for index, step in enumerate(timeline["steps"]):
        assert step["status"]
        assert step["title"]
        assert step["summary"]
        assert step["at"]
        assert step["signature"]["alg"] == "HMAC-SHA256"
        assert len(step["signature"]["value"]) == 64
        assert (
            step["provenance"]["source_ref"] == f"omnicare://demo/runs/{run_id}/steps/{index + 1}"
        )
        assert step["provenance"]["event_id"]
    assert timeline["steps"][1]["status"] == "blocked"
    assert timeline["steps"][3]["provenance"]["refund_id"] == refund_id


@pytest.mark.asyncio
async def test_steps_are_idempotent_and_do_not_duplicate_side_effects(client) -> None:
    run_id = await _start_run(client)
    first_logistics = await _run_step(client, run_id, "logistics")
    await _run_step(client, run_id, "refund")
    first_escalate = await _run_step(client, run_id, "escalate")
    await _run_step(client, run_id, "approve")

    repeat_logistics = await _run_step(client, run_id, "logistics")
    repeat_escalate = await _run_step(client, run_id, "escalate")
    assert repeat_logistics == first_logistics
    assert repeat_escalate == first_escalate

    async with SessionLocal() as db:
        refunds = await db.scalar(
            select(func.count(RefundRequest.id)).where(RefundRequest.order_id == DEMO_ORDER)
        )
        audits = await db.scalar(
            select(func.count(RefundAudit.id)).where(RefundAudit.order_id == DEMO_ORDER)
        )
    assert refunds == 1
    assert audits == 1

    replay = (await client.get(f"/api/v1/demo/runs/{run_id}/replay")).json()
    assert replay["verified"] is True
    assert len(replay["steps"]) == 4


@pytest.mark.asyncio
async def test_out_of_order_step_is_rejected(client) -> None:
    run_id = await _start_run(client)
    response = await client.post(f"/api/v1/demo/runs/{run_id}/steps/refund")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "step_out_of_order"


@pytest.mark.asyncio
async def test_starting_a_new_run_supersedes_the_previous_one(client) -> None:
    first_run = await _start_run(client)
    second_run = await _start_run(client)

    stale = await client.post(f"/api/v1/demo/runs/{first_run}/steps/logistics")
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "run_superseded"

    fresh = await _run_step(client, second_run, "logistics")
    assert fresh["step"] == 1
    assert fresh["status"] == "ok"


@pytest.mark.asyncio
async def test_starting_a_run_resets_previous_refunds_and_audits(client) -> None:
    first_run = await _start_run(client)
    for key in ("logistics", "refund", "escalate", "approve"):
        await _run_step(client, first_run, key)

    second_run = await _start_run(client)
    async with SessionLocal() as db:
        order = await db.get(Order, DEMO_ORDER)
        assert order is not None
        assert order.status == "shipped"
        assert Decimal(str(order.refunded_amount)) == Decimal("0.00")
        refunds = await db.scalar(
            select(func.count(RefundRequest.id)).where(RefundRequest.order_id == DEMO_ORDER)
        )
        audits = await db.scalar(
            select(func.count(RefundAudit.id)).where(RefundAudit.order_id == DEMO_ORDER)
        )
    assert refunds == 0
    assert audits == 0

    # The new run is still fully usable after the reset.
    body = await _run_step(client, second_run, "logistics")
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_replay_detects_a_tampered_event_payload(client) -> None:
    run_id, _ = await _run_full_flow(client)

    async with SessionLocal() as db:
        events = (
            await db.scalars(
                select(AgentEvent)
                .where(
                    AgentEvent.run_id == run_id,
                    AgentEvent.event_type == "demo_step",
                )
                .order_by(AgentEvent.created_at)
            )
        ).all()
        assert len(events) == 4
        tampered = dict(events[1].payload)
        tampered["status"] = "approved"
        events[1].payload = tampered
        await db.commit()

    replay = await client.get(f"/api/v1/demo/runs/{run_id}/replay")
    assert replay.status_code == 200
    timeline = replay.json()
    assert timeline["verified"] is False
    assert len(timeline["steps"]) == 4


@pytest.mark.asyncio
async def test_run_starts_are_rate_limited(client) -> None:
    service = app.state.demo_service
    original_limit = service.rate_limit_starts
    service.rate_limit_starts = 2
    try:
        assert (await client.post("/api/v1/demo/runs")).status_code == 200
        assert (await client.post("/api/v1/demo/runs")).status_code == 200
        limited = await client.post("/api/v1/demo/runs")
        assert limited.status_code == 429
        assert limited.json()["detail"]["code"] == "demo_rate_limited"
    finally:
        service.rate_limit_starts = original_limit
        service._starts.clear()
