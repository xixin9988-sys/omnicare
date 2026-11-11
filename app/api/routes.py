from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_supervisor
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

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "omnicare-api", "time": datetime.now(UTC).isoformat()}


@router.post("/sessions", status_code=201)
async def create_session(payload: SessionCreate, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, payload.user_id)
    if user is None:
        user = User(id=payload.user_id, display_name="OmniCare Demo User")
        db.add(user)
        await db.flush()
    session = ConversationSession(user_id=payload.user_id)
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
    payload: ChatRequest, supervisor=Depends(get_supervisor), db: AsyncSession = Depends(get_db)
):
    session = await db.get(ConversationSession, payload.session_id)
    if session is None or session.user_id != payload.user_id or session.status != "active":
        raise HTTPException(
            status_code=404, detail={"code": "session_not_found", "message": "会话不存在或不可用。"}
        )
    result = await supervisor.run(
        user_id=payload.user_id, session_id=payload.session_id, message=payload.message, db=db
    )
    return result


@router.get("/products", response_model=list[ProductOut])
async def products(q: str = Query(default="", max_length=120), db: AsyncSession = Depends(get_db)):
    stmt = select(Product).where(Product.active.is_(True))
    if q:
        stmt = stmt.where(Product.name.contains(q))
    return list((await db.scalars(stmt.limit(30))).all())


@router.get("/metrics/summary")
async def metrics_summary(db: AsyncSession = Depends(get_db)):
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
    limit: int = Query(default=100, ge=1, le=500), db: AsyncSession = Depends(get_db)
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
    limit: int = Query(default=100, ge=1, le=500), db: AsyncSession = Depends(get_db)
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


@router.websocket("/ws/monitor")
async def monitor_ws(websocket: WebSocket):
    await broker.connect(websocket)
    try:
        while True:
            # Keepalive/optional client messages. Agent events are pushed by EventBroker.
            await websocket.receive_text()
    except WebSocketDisconnect:
        broker.disconnect(websocket)
    except Exception:
        broker.disconnect(websocket)
