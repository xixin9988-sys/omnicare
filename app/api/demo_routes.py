"""HTTP routes for the deterministic 60-second demo.

The demo is a sandbox scenario over the existing policy/event stack, so these routes carry no
customer identity: they always act on the fixed demo user/order configured in
``Settings`` (the same identity the public compose stack serves). Everything else is normal
FastAPI/SQLAlchemy plumbing; domain errors become ``{"detail": {"code", "message"}}``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.services.demo import DemoDisabled, DemoError, DemoService

router = APIRouter(prefix="/demo", tags=["demo"])


def _fail(error: DemoError) -> None:
    raise HTTPException(
        status_code=error.status_code, detail={"code": error.code, "message": error.message}
    ) from error


async def get_demo_service(request: Request) -> DemoService:
    """Return the app-wide demo service, or 404 when the environment disables the demo."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    if not settings.demo_enabled:
        _fail(DemoDisabled("演示模式未开启。"))
    service = getattr(request.app.state, "demo_service", None)
    if service is None:
        # Defensive fallback for ASGI hosts that do not run the app lifespan.
        service = DemoService(settings)
        request.app.state.demo_service = service
    return service


@router.get("/manifest")
async def demo_manifest(service: Annotated[DemoService, Depends(get_demo_service)]) -> dict:
    """Static scenario description (loads before any run; unauthenticated by design)."""
    return service.manifest()


@router.post("/runs")
async def start_demo_run(
    request: Request,
    service: Annotated[DemoService, Depends(get_demo_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    client_key = request.client.host if request.client else "unknown"
    try:
        return await service.start_run(db, client_key=client_key)
    except DemoError as error:
        _fail(error)


@router.post("/runs/{run_id}/steps/logistics")
async def demo_step_logistics(
    run_id: str,
    service: Annotated[DemoService, Depends(get_demo_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        return await service.run_step(db, run_id=run_id, key="logistics")
    except DemoError as error:
        _fail(error)


@router.post("/runs/{run_id}/steps/refund")
async def demo_step_refund(
    run_id: str,
    service: Annotated[DemoService, Depends(get_demo_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        return await service.run_step(db, run_id=run_id, key="refund")
    except DemoError as error:
        _fail(error)


@router.post("/runs/{run_id}/steps/escalate")
async def demo_step_escalate(
    run_id: str,
    service: Annotated[DemoService, Depends(get_demo_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        return await service.run_step(db, run_id=run_id, key="escalate")
    except DemoError as error:
        _fail(error)


@router.post("/runs/{run_id}/steps/approve")
async def demo_step_approve(
    run_id: str,
    service: Annotated[DemoService, Depends(get_demo_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        return await service.run_step(db, run_id=run_id, key="approve")
    except DemoError as error:
        _fail(error)


@router.get("/runs/{run_id}/replay")
async def demo_replay(
    run_id: str,
    service: Annotated[DemoService, Depends(get_demo_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        return await service.replay(db, run_id=run_id)
    except DemoError as error:
        _fail(error)
