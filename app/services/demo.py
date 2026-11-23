"""Deterministic 60-second demo scenario on the real policy/event/console stack.

Every step runs through the production :class:`ToolRegistry` and
:class:`~app.governance.local_guard.LocalPolicyGuard`, is persisted as an ``AgentEvent`` and is
HMAC-signed over a canonical JSON payload, so ``GET /demo/runs/{run_id}/replay`` can prove the
timeline was not edited after the fact. Signing keys come from the same attestation secret the
memory-provenance chain uses; the key never appears in a response or in an event payload.

Only one run is active at a time. Starting a new run resets the fixed demo order (ORD-001),
deletes the previous refund/audit rows and supersedes the previous in-memory run; steps of a
superseded run answer 409. All run state lives in this process: the demo stack is single-worker.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select

from app.core.config import Settings
from app.core.events import broker, utc_now
from app.governance.local_guard import LocalPolicyGuard
from app.models.entities import (
    AgentEvent,
    ConversationSession,
    Order,
    RefundAudit,
    RefundRequest,
    User,
)
from app.services.attestation import resolve_attestation_secret
from app.services.refund_approval import RefundApprovalError, decide_refund
from app.tools.ecommerce import build_registry
from app.tools.registry import ToolContext, ToolRegistry

DEMO_STEP_EVENT = "demo_step"
SIGNATURE_ALG = "HMAC-SHA256"
SIGNATURE_VERSION = "omnicare-demo-step-v1"
SIGNATURE_SHORT_LENGTH = 12

MANIFEST_TITLE = "60 秒，看清一次被拦住的退款。"
MANIFEST_SUBTITLE = "MCP 工具 → 策略拦截 → 人工复核 → 签名回放"
DEMO_DURATION_SECONDS = 60
REFUND_REASON = "演示场景：商品与描述不符，申请退款"

MCP_NAME = "OmniCare Business Tools"
MCP_TRANSPORT = "streamable-http"
MCP_ENDPOINT = "/mcp"

#: Steps of the fixed scenario. Labels/hints/narrations are static so the manifest needs no DB.
STEP_DEFS: tuple[dict[str, Any], ...] = (
    {
        "index": 1,
        "key": "logistics",
        "label": "查物流",
        "hint": "MCP 只读工具 check_logistics",
        "narration": "调用 MCP 只读工具查询 ORD-001 的物流轨迹：顺丰速运 SF-ORD-001。",
        "title": "物流查询成功",
        "tool": "get_logistics",
    },
    {
        "index": 2,
        "key": "refund",
        "label": "申请退款",
        "hint": "MCP 业务写工具 request_refund · 100000 元",
        "narration": "为 ORD-001 申请退款 100000 元，命中高额退款人工审批规则，被策略拦截。",
        "title": "策略拦截",
        "tool": "request_refund",
    },
    {
        "index": 3,
        "key": "escalate",
        "label": "转人工",
        "hint": "人工接管后重新提交退款申请",
        "narration": "已转人工复核：退款进入 pending_review，等待 demo_operator 审批。",
        "title": "已转人工复核",
        "tool": "request_refund",
    },
    {
        "index": 4,
        "key": "approve",
        "label": "批准放行",
        "hint": "demo_operator 审批通过",
        "narration": "demo_operator 审批通过：退款 100000 元放行（approved）。",
        "title": "人工审批通过 · 放行",
        "tool": "decide_refund",
    },
)
STEP_BY_INDEX = {step["index"]: step for step in STEP_DEFS}
STEP_INDEX_BY_KEY = {step["key"]: step["index"] for step in STEP_DEFS}


class DemoError(Exception):
    """Domain failure translated to ``{detail: {code, message}}`` by the routes."""

    code = "demo_error"
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DemoDisabled(DemoError):
    code = "demo_disabled"
    status_code = 404


class DemoRunNotFound(DemoError):
    code = "run_not_found"
    status_code = 404


class DemoRunSuperseded(DemoError):
    code = "run_superseded"
    status_code = 409


class DemoStepOutOfOrder(DemoError):
    code = "step_out_of_order"
    status_code = 409


class DemoInvalidStepState(DemoError):
    code = "invalid_step_state"
    status_code = 409


class DemoRateLimited(DemoError):
    code = "demo_rate_limited"
    status_code = 429


@dataclass
class DemoRunState:
    """In-process state of one demo run (the DB holds the auditable side effects)."""

    run_id: str
    session_id: str
    started_at: datetime
    steps: dict[int, dict[str, Any]] = field(default_factory=dict)
    refund_id: str | None = None
    superseded: bool = False


def _iso(value: datetime) -> str:
    """ISO-8601 with an explicit UTC offset (SQLite returns naive datetimes)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _amount(value: object) -> str:
    return f"{Decimal(str(value)):.2f}"


class DemoService:
    """Drives the fixed ORD-001 scenario; one active run per process."""

    #: Keep well above the browser flow (1 start) so tests can start runs repeatedly.
    rate_limit_starts = 30
    rate_limit_window_seconds = 60.0

    def __init__(self, settings: Settings, *, registry: ToolRegistry | None = None) -> None:
        self.settings = settings
        self.registry = registry or build_registry()
        self.policy_version = settings.mem_guard_policy_version
        self.user_id = settings.demo_user_id
        self.order_id = settings.demo_order_id
        self.order_total = Decimal(str(settings.demo_order_total))
        self.refund_amount = Decimal(str(settings.demo_refund_amount))
        self.high_value_threshold = Decimal(str(settings.demo_high_value_threshold))
        self.operator_id = settings.demo_operator_id
        self.secret = resolve_attestation_secret(settings)
        self.guard = LocalPolicyGuard(self.policy_version, self.high_value_threshold)
        self._runs: dict[str, DemoRunState] = {}
        self._active_run_id: str | None = None
        self._starts: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ public API
    def manifest(self) -> dict[str, Any]:
        """Static description of the scenario; works before any run exists."""
        return {
            "title": MANIFEST_TITLE,
            "subtitle": MANIFEST_SUBTITLE,
            "order": {
                "id": self.order_id,
                "total_amount": _amount(self.order_total),
                "status": "shipped",
                "carrier": "顺丰速运",
                "tracking_no": "SF-ORD-001",
            },
            "amount": _amount(self.refund_amount),
            "duration_seconds": DEMO_DURATION_SECONDS,
            "mcp": {
                "name": MCP_NAME,
                "transport": MCP_TRANSPORT,
                "endpoint": MCP_ENDPOINT,
                "tools": [
                    {
                        "name": "check_logistics",
                        "label": "查物流",
                        "description": "查询当前用户订单的承运与轨迹信息（只读）。",
                        "read_only": True,
                        "arguments": [
                            {
                                "name": "order_id",
                                "type": "string",
                                "required": False,
                                "description": "订单号；省略时查询最近一笔订单",
                            }
                        ],
                    },
                    {
                        "name": "request_refund",
                        "label": "申请退款",
                        "description": "为当前用户订单创建待审核退款申请（业务写操作，先过治理策略）。",
                        "read_only": False,
                        "arguments": [
                            {
                                "name": "order_id",
                                "type": "string",
                                "required": True,
                                "description": "订单号",
                            },
                            {
                                "name": "amount",
                                "type": "decimal",
                                "required": True,
                                "description": "退款金额（元）",
                            },
                            {
                                "name": "reason",
                                "type": "string",
                                "required": False,
                                "description": "申请原因",
                            },
                        ],
                    },
                ],
            },
            "steps": [
                {
                    "index": step["index"],
                    "key": step["key"],
                    "label": step["label"],
                    "hint": step["hint"],
                    "narration": step["narration"],
                }
                for step in STEP_DEFS
            ],
        }

    async def start_run(self, db, *, client_key: str = "unknown") -> dict[str, Any]:
        """Reset the demo data and open a fresh run (superseding the previous one)."""
        self._check_start_rate_limit(client_key)
        async with self._lock_for_current_loop():
            started_at = utc_now()
            session = await self._reset_demo_data(db, started_at)
            previous = self._runs.get(self._active_run_id or "")
            state = DemoRunState(run_id=str(uuid4()), session_id=session.id, started_at=started_at)
            self._runs[state.run_id] = state
            self._active_run_id = state.run_id
            if previous is not None:
                previous.superseded = True
            order = await db.get(Order, self.order_id)
            return {
                "run_id": state.run_id,
                "started_at": _iso(state.started_at),
                "session_id": state.session_id,
                "order": self._order_payload(order),
                "amount": _amount(self.refund_amount),
            }

    async def run_step(self, db, *, run_id: str, key: str) -> dict[str, Any]:
        """Run one scenario step; completed steps are idempotent replays of their result."""
        index = STEP_INDEX_BY_KEY.get(key)
        if index is None:
            raise DemoInvalidStepState(f"未知演示步骤：{key}")
        async with self._lock_for_current_loop():
            state = self._require_run(run_id)
            if index in state.steps:
                return state.steps[index]
            if index != len(state.steps) + 1:
                raise DemoStepOutOfOrder("请按顺序执行演示步骤；当前步骤尚未完成。")
            handler = getattr(self, "_step_" + key)
            result = await handler(db, state)
            state.steps[index] = result
            return result

    async def replay(self, db, *, run_id: str) -> dict[str, Any]:
        """Rebuild the timeline from persisted AgentEvents and verify every HMAC."""
        state = self._runs.get(run_id)
        if state is None:
            raise DemoRunNotFound("演示运行不存在或已过期。")
        rows = list(
            (
                await db.scalars(
                    select(AgentEvent)
                    .where(
                        AgentEvent.run_id == run_id,
                        AgentEvent.event_type == DEMO_STEP_EVENT,
                    )
                    .order_by(AgentEvent.created_at, AgentEvent.id)
                )
            ).all()
        )
        verified = True
        steps: list[dict[str, Any]] = []
        seen: set[int] = set()
        for event in rows:
            payload = dict(event.payload) if isinstance(event.payload, dict) else {}
            signature = payload.get("signature")
            provenance = payload.get("provenance")
            if not isinstance(signature, dict):
                signature = {"alg": SIGNATURE_ALG, "value": "", "short": ""}
            if not isinstance(provenance, dict):
                provenance = {}
            index = _safe_int(payload.get("step"))
            if index is not None:
                seen.add(index)
            if not self._verify(payload, event):
                verified = False
            steps.append(
                {
                    "index": index,
                    "key": str(payload.get("key") or ""),
                    "label": str(payload.get("label") or ""),
                    "status": str(payload.get("status") or ""),
                    "title": str(payload.get("title") or ""),
                    "summary": str(payload.get("summary") or ""),
                    "at": _iso(event.created_at),
                    "signature": signature,
                    "provenance": provenance,
                }
            )
        steps.sort(key=lambda item: item["index"] if item["index"] is not None else 0)
        # A deleted event must also invalidate the proof, not silently shrink the timeline.
        if len(rows) != len(state.steps) or seen != set(state.steps):
            verified = False
        return {"run_id": run_id, "verified": verified, "steps": steps}

    # ------------------------------------------------------------------ steps
    async def _step_logistics(self, db, state: DemoRunState) -> dict[str, Any]:
        definition = STEP_BY_INDEX[1]
        result = await self.registry.execute(
            "get_logistics",
            {"order_id": self.order_id},
            self._tool_context(db, state),
        )
        if not result.success:
            raise DemoInvalidStepState("物流查询失败，请重新开始演示。")
        carrier = result.data.get("carrier") or ""
        tracking_no = result.data.get("tracking_no") or ""
        status = result.data.get("logistics_status") or result.data.get("status") or ""
        return await self._finish_step(
            db,
            state,
            definition,
            status="ok",
            data={"carrier": carrier, "tracking_no": tracking_no, "status": status},
            summary=f"{carrier} {tracking_no} · {status}".strip(),
        )

    async def _step_refund(self, db, state: DemoRunState) -> dict[str, Any]:
        definition = STEP_BY_INDEX[2]
        result = await self.registry.execute(
            "request_refund",
            self._refund_args(),
            self._tool_context(db, state),
        )
        if result.success:
            await db.rollback()
            raise DemoInvalidStepState("退款申请未被策略拦截，演示状态异常，请重置后重试。")
        if result.data.get("governance_decision") != "quarantine":
            await db.rollback()
            raise DemoInvalidStepState("退款申请未按预期被高额退款策略拦截。")
        policy = {
            "rule_id": result.data.get("rule_id") or "high-value-refund",
            "rule_label": result.data.get("rule_label") or "高额退款必须人工审批",
            "reason_code": result.data.get("reason_code") or "high_value_refund_requires_human",
            "threshold": _amount(self.high_value_threshold),
            "detail": result.data.get("detail") or "",
            "matched_rules": list(result.data.get("matched_rules") or []),
        }
        return await self._finish_step(
            db,
            state,
            definition,
            status="blocked",
            data={
                "amount": _amount(self.refund_amount),
                "order_id": self.order_id,
                "decision": "quarantine",
            },
            policy=policy,
            summary=f"策略拦截：{policy['rule_label']}（{policy['rule_id']}）",
        )

    async def _step_escalate(self, db, state: DemoRunState) -> dict[str, Any]:
        definition = STEP_BY_INDEX[3]
        result = await self.registry.execute(
            "request_refund",
            self._refund_args(),
            self._tool_context(db, state, human_escalated=True),
        )
        if not result.success:
            await db.rollback()
            raise DemoInvalidStepState(f"转人工复核失败：{result.error_code}。")
        refund_id = result.data.get("refund_request_id")
        refund = await db.get(RefundRequest, refund_id) if refund_id else None
        if refund is None:
            await db.rollback()
            raise DemoInvalidStepState("退款申请创建成功但无法读取，请重置后重试。")
        state.refund_id = refund.id
        return await self._finish_step(
            db,
            state,
            definition,
            status="pending_review",
            data={
                "refund_id": refund.id,
                "amount": _amount(refund.amount),
                "status": refund.status,
            },
            extra={"refund": self._refund_payload(refund)},
            refund_id=refund.id,
            summary="退款申请已转人工复核（pending_review），等待 demo_operator 审批。",
        )

    async def _step_approve(self, db, state: DemoRunState) -> dict[str, Any]:
        definition = STEP_BY_INDEX[4]
        if state.refund_id is None:
            raise DemoInvalidStepState("没有待审批的退款申请，请先完成转人工复核。")
        try:
            refund, transition = await decide_refund(
                db,
                refund_id=state.refund_id,
                decision="approve",
                actor=self.operator_id,
                reason="60 秒演示：人工审批通过",
            )
            audit = await db.scalar(
                select(RefundAudit)
                .where(RefundAudit.refund_id == refund.id)
                .order_by(RefundAudit.created_at.desc(), RefundAudit.id)
                .limit(1)
            )
        except RefundApprovalError as error:
            await db.rollback()
            raise DemoInvalidStepState(error.message) from error
        if audit is None:
            await db.rollback()
            raise DemoInvalidStepState("审批审计记录缺失，请重置后重试。")
        return await self._finish_step(
            db,
            state,
            definition,
            status="approved",
            data={
                "refund_id": refund.id,
                "amount": _amount(refund.amount),
                "status": refund.status,
                "decided_by": refund.decided_by,
            },
            extra={
                "refund": self._refund_payload(refund),
                "audit": {
                    "id": audit.id,
                    "from_status": transition.from_status,
                    "to_status": transition.to_status,
                    "decision": transition.decision,
                    "actor": transition.actor,
                },
            },
            refund_id=refund.id,
            summary=f"{self.operator_id} 已批准退款 {_amount(refund.amount)} 元（approved）。",
        )

    # ------------------------------------------------------------------ signing
    def _canonical(self, material: dict[str, Any]) -> bytes:
        return json.dumps(
            material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def _digest(self, material: dict[str, Any]) -> str:
        return hmac.new(
            self.secret.encode("utf-8"), self._canonical(material), hashlib.sha256
        ).hexdigest()

    def sign(self, payload: dict[str, Any]) -> dict[str, str]:
        """HMAC-SHA256 over canonical JSON; the signature field itself is excluded."""
        digest = self._digest(payload)
        return {
            "alg": SIGNATURE_ALG,
            "value": digest,
            "short": digest[:SIGNATURE_SHORT_LENGTH],
        }

    def _verify(self, payload: dict[str, Any], event: AgentEvent) -> bool:
        signature = payload.get("signature")
        if not isinstance(signature, dict):
            return False
        material = {key: value for key, value in payload.items() if key != "signature"}
        expected = self._digest(material)
        if not hmac.compare_digest(str(signature.get("value") or ""), expected):
            return False
        provenance = payload.get("provenance")
        if not isinstance(provenance, dict):
            return False
        # Bind the payload to the row it was loaded from: moving a payload between events, or
        # swapping the event id, is a mismatch even if the digest itself was recomputed.
        return (
            str(payload.get("run_id") or "") == str(event.run_id)
            and str(provenance.get("event_id") or "") == str(event.id)
            and payload.get("version") == SIGNATURE_VERSION
        )

    # ------------------------------------------------------------------ internals
    def _require_run(self, run_id: str) -> DemoRunState:
        state = self._runs.get(run_id)
        if state is None:
            raise DemoRunNotFound("演示运行不存在或已过期。")
        if state.superseded:
            raise DemoRunSuperseded("该演示运行已被新的运行取代，请使用最新的运行。")
        return state

    def _lock_for_current_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _check_start_rate_limit(self, client_key: str) -> None:
        now = time.monotonic()
        hits = self._starts[client_key]
        while hits and now - hits[0] > self.rate_limit_window_seconds:
            hits.popleft()
        if len(hits) >= self.rate_limit_starts:
            raise DemoRateLimited("演示启动过于频繁，请稍后再试。")
        hits.append(now)

    def _refund_args(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "amount": str(self.refund_amount),
            "reason": REFUND_REASON,
        }

    def _tool_context(
        self, db, state: DemoRunState, *, human_escalated: bool = False
    ) -> ToolContext:
        async def emit_event(
            event_type: str, tool_name: str, status: str, payload: dict | None = None
        ) -> None:
            await self._persist_event(
                db,
                state,
                event_type=event_type,
                agent="mcp",
                status=status,
                payload={"tool": tool_name, **(payload or {})},
            )

        return ToolContext(
            user_id=self.user_id,
            session_id=state.session_id,
            run_id=state.run_id,
            db=db,
            governance=self.guard,
            emit_event=emit_event,
            human_escalated=human_escalated,
        )

    async def _persist_event(
        self,
        db,
        state: DemoRunState,
        *,
        event_type: str,
        agent: str,
        status: str,
        payload: dict[str, Any],
        created_at: datetime | None = None,
        event_id: str | None = None,
    ) -> AgentEvent:
        at = created_at or utc_now()
        event = AgentEvent(
            id=event_id or str(uuid4()),
            run_id=state.run_id,
            session_id=state.session_id,
            event_type=event_type,
            agent=agent,
            status=status,
            payload=payload,
            created_at=at,
        )
        db.add(event)
        await broker.publish(
            {
                "id": event.id,
                "run_id": state.run_id,
                "session_id": state.session_id,
                "event_type": event_type,
                "agent": agent,
                "status": status,
                "payload": payload,
                "created_at": _iso(at),
            }
        )
        return event

    async def _finish_step(
        self,
        db,
        state: DemoRunState,
        definition: dict[str, Any],
        *,
        status: str,
        data: dict[str, Any],
        summary: str,
        policy: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
        refund_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist the signed step event, commit the step's DB changes and build the response."""
        index = definition["index"]
        at = utc_now()
        event_id = str(uuid4())
        provenance: dict[str, Any] = {
            "source_ref": f"omnicare://demo/runs/{state.run_id}/steps/{index}",
            "policy_version": self.policy_version,
            "event_id": event_id,
            "order_id": self.order_id,
            "tool": definition["tool"],
        }
        if refund_id:
            provenance["refund_id"] = refund_id
        payload: dict[str, Any] = {
            "version": SIGNATURE_VERSION,
            "run_id": state.run_id,
            "session_id": state.session_id,
            "step": index,
            "key": definition["key"],
            "label": definition["label"],
            "status": status,
            "title": definition["title"],
            "summary": summary,
            "at": _iso(at),
            "data": data,
            "policy": policy,
            "provenance": provenance,
        }
        signature = self.sign(payload)
        stored_payload = dict(payload)
        stored_payload["signature"] = signature
        event = await self._persist_event(
            db,
            state,
            event_type=DEMO_STEP_EVENT,
            agent="demo",
            status=status,
            payload=stored_payload,
            created_at=at,
            event_id=event_id,
        )
        await db.commit()
        response: dict[str, Any] = {
            "step": index,
            "key": definition["key"],
            "status": status,
            "title": definition["title"],
            "data": data,
            "policy": policy,
            "event": {
                "id": event.id,
                "event_type": DEMO_STEP_EVENT,
                "created_at": _iso(at),
            },
            "signature": signature,
            "provenance": provenance,
        }
        if extra:
            response.update(extra)
        return response

    async def _reset_demo_data(self, db, started_at: datetime) -> ConversationSession:
        """Reset the fixed user/order and remove the previous run's refunds and audits.

        Deletes children before parents (refund_audit -> refund_requests); PostgreSQL enforces
        the foreign key that SQLite does not, so this order is the portable one.
        """
        user = await db.get(User, self.user_id)
        if user is None:
            db.add(User(id=self.user_id, display_name="演示用户"))
            await db.flush()
        await db.execute(delete(RefundAudit).where(RefundAudit.order_id == self.order_id))
        await db.execute(delete(RefundRequest).where(RefundRequest.order_id == self.order_id))
        order = await db.get(Order, self.order_id)
        if order is None:
            order = Order(id=self.order_id, user_id=self.user_id)
            db.add(order)
        order.user_id = self.user_id
        order.status = "shipped"
        order.carrier = "顺丰速运"
        order.tracking_no = "SF-ORD-001"
        order.logistics_status = "运输中，预计明日送达"
        order.total_amount = self.order_total
        order.refunded_amount = Decimal("0.00")
        order.created_at = started_at
        session = ConversationSession(user_id=self.user_id, status="active", created_at=started_at)
        db.add(session)
        await db.flush()
        await db.commit()
        return session

    def _order_payload(self, order: Order | None) -> dict[str, Any]:
        return {
            "id": self.order_id,
            "total_amount": _amount(self.order_total),
            "status": order.status if order is not None else "shipped",
            "carrier": order.carrier if order is not None else "顺丰速运",
            "tracking_no": order.tracking_no if order is not None else "SF-ORD-001",
        }

    def _refund_payload(self, refund: RefundRequest) -> dict[str, Any]:
        return {
            "id": refund.id,
            "status": refund.status,
            "amount": _amount(refund.amount),
            "decided_by": refund.decided_by,
        }


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DEMO_STEP_EVENT",
    "SIGNATURE_ALG",
    "DemoDisabled",
    "DemoError",
    "DemoInvalidStepState",
    "DemoRateLimited",
    "DemoRunNotFound",
    "DemoRunState",
    "DemoRunSuperseded",
    "DemoService",
    "DemoStepOutOfOrder",
]
