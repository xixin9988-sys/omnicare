"""HTTP and WebSocket routes.

Identity is never read from a request body: every mutating route resolves a :class:`Principal`
from the ``Authorization`` bearer token (or the dev header) and uses that user id. Admin routes
require the ``admin`` role; the monitor socket requires the same.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    Principal,
    authenticate_ws,
    get_principal,
    get_supervisor,
    require_admin,
)
from app.core.config import get_settings
from app.core.db import get_db
from app.core.events import broker
from app.models.entities import (
    AgentEvent,
    AgentRun,
    ConversationSession,
    GovernanceIncident,
    Product,
    User,
)
from app.schemas.chat import ChatRequest, ChatResponse, ProductOut, SessionCreate
from app.schemas.refunds import RefundAuditOut, RefundDecisionRequest, RefundOut
from app.services.refund_approval import (
    RefundApprovalError,
    audit_trail,
    decide_refund,
    list_refunds,
)

router = APIRouter()

_DECISION_STATUS = {
    "refund_not_found": status.HTTP_404_NOT_FOUND,
    "order_not_found": status.HTTP_404_NOT_FOUND,
    "actor_required": status.HTTP_400_BAD_REQUEST,
}


@router.get("/health")
async def health():
    return {"status": "ok", "service": "omnicare-api", "time": datetime.now(UTC).isoformat()}


@router.post("/sessions", status_code=201)
async def create_session(
    payload: SessionCreate,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    # The body may echo a user id, but it is only a claim: the token decides who this is.
    if payload.user_id and payload.user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "user_id_mismatch",
                "message": "The authenticated user does not match the requested user id.",
            },
        )
    user = await db.get(User, principal.user_id)
    if user is None:
        user = User(id=principal.user_id, display_name="OmniCare User")
        db.add(user)
        await db.flush()
    session = ConversationSession(user_id=principal.user_id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {
        "session_id": session.id,
        "user_id": session.user_id,
        "status": session.status,
        "created_at": session.created_at,
    }


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    supervisor=Depends(get_supervisor),
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    if payload.user_id and payload.user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "user_id_mismatch",
                "message": "The authenticated user does not match the requested user id.",
            },
        )
    session = await db.get(ConversationSession, payload.session_id)
    if session is None or session.user_id != principal.user_id or session.status != "active":
        raise HTTPException(
            status_code=404,
            detail={"code": "session_not_found", "message": "Session not found or not usable."},
        )
    return await supervisor.run(
        user_id=principal.user_id,
        session_id=payload.session_id,
        message=payload.message,
        db=db,
    )


@router.get("/products", response_model=list[ProductOut])
async def products(q: str = Query(default="", max_length=120), db: AsyncSession = Depends(get_db)):
    stmt = select(Product).where(Product.active.is_(True))
    if q:
        stmt = stmt.where(Product.name.contains(q))
    return list((await db.scalars(stmt.limit(30))).all())


@router.get("/metrics/summary")
async def metrics_summary(
    _: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    total_runs = int(await db.scalar(select(func.count(AgentRun.id))) or 0)
    blocked = int(
        await db.scalar(select(func.count(AgentRun.id)).where(AgentRun.status == "blocked")) or 0
    )
    total_tokens = int(
        await db.scalar(select(func.coalesce(func.sum(AgentRun.total_tokens), 0))) or 0
    )
    avg_latency = float(
        await db.scalar(select(func.coalesce(func.avg(AgentRun.latency_ms), 0))) or 0
    )
    agents = (
        await db.execute(
            select(AgentRun.selected_agent, func.count(AgentRun.id)).group_by(
                AgentRun.selected_agent
            )
        )
    ).all()
    incidents = int(await db.scalar(select(func.count(GovernanceIncident.id))) or 0)
    return {
        "total_runs": total_runs,
        "blocked_runs": blocked,
        "governance_incidents": incidents,
        "total_tokens": total_tokens,
        "avg_latency_ms": round(avg_latency, 2),
        "agent_counts": {name: count for name, count in agents},
    }


@router.get("/events")
async def recent_events(
    _: Principal = Depends(require_admin),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(select(AgentEvent).order_by(desc(AgentEvent.created_at)).limit(limit))
    ).all()
    return [
        {
            "id": e.id,
            "run_id": e.run_id,
            "session_id": e.session_id,
            "event_type": e.event_type,
            "agent": e.agent,
            "status": e.status,
            "payload": e.payload,
            "created_at": e.created_at,
        }
        for e in rows
    ]


@router.get("/governance/incidents")
async def governance_incidents(
    _: Principal = Depends(require_admin),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(GovernanceIncident).order_by(desc(GovernanceIncident.created_at)).limit(limit)
        )
    ).all()
    return [
        {
            "id": x.id,
            "run_id": x.run_id,
            "user_id": x.user_id,
            "category": x.category,
            "decision": x.decision,
            "reason_code": x.reason_code,
            "detail": x.detail,
            "created_at": x.created_at,
        }
        for x in rows
    ]


@router.get("/refunds/mine", response_model=list[RefundOut])
async def my_refunds(
    principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    """A customer only ever sees their own refund requests."""
    return await list_refunds(db, user_id=principal.user_id)


@router.get("/refunds", response_model=list[RefundOut])
async def admin_list_refunds(
    status_filter: str | None = Query(default=None, alias="status", max_length=40),
    limit: int = Query(default=100, ge=1, le=500),
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await list_refunds(db, status=status_filter, limit=limit)


@router.post("/refunds/{refund_id}/decision", response_model=RefundOut)
async def decide_refund_route(
    refund_id: str,
    payload: RefundDecisionRequest,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Approve, reject or settle one refund; the requester can never decide their own."""
    try:
        refund, _transition = await decide_refund(
            db,
            refund_id=refund_id,
            decision=payload.decision,
            actor=principal.user_id,
            reason=payload.reason,
        )
    except RefundApprovalError as error:
        raise HTTPException(
            status_code=_DECISION_STATUS.get(error.code, status.HTTP_409_CONFLICT),
            detail={"code": error.code, "message": error.message},
        ) from error
    await db.commit()
    await db.refresh(refund)
    return refund


@router.get("/refunds/{refund_id}/audit", response_model=list[RefundAuditOut])
async def refund_audit(
    refund_id: str,
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await audit_trail(db, refund_id)


@router.websocket("/ws/monitor")
async def monitor_ws(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    admin_key: str | None = Query(default=None),
):
    settings = get_settings()
    principal = authenticate_ws(token=token, admin_key=admin_key, settings=settings)
    if principal is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if not await broker.connect(websocket):
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        broker.disconnect(websocket)
    except Exception:
        broker.disconnect(websocket)
