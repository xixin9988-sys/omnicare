from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.core.config import get_settings
from app.core.db import init_db, SessionLocal
from app.core.logging import configure_logging
from app.governance.factory import create_governance_adapter
from app.integrations.n8n import N8NClient
from app.memory.factory import create_memory_adapter
from app.models.entities import User, Product, Order
from app.providers.factory import create_provider
from app.services.supervisor import AgentSupervisor
from app.tools.ecommerce import build_registry
from sqlalchemy import select
from decimal import Decimal

settings = get_settings()
configure_logging()


async def seed_demo_data() -> None:
    async with SessionLocal() as db:
        user = await db.get(User, settings.demo_user_id)
        if user is None:
            db.add(User(id=settings.demo_user_id, display_name="演示用户"))
        exists = await db.scalar(select(Product.id).limit(1))
        if exists is None:
            db.add_all([
                Product(id="SKU-SHOE-001", sku="SHOE-001", name="轻云缓震跑鞋", description="日常跑步轻量缓震鞋", price=Decimal("399.00"), stock=42),
                Product(id="SKU-EAR-001", sku="EAR-001", name="降噪蓝牙耳机", description="主动降噪，通勤使用", price=Decimal("599.00"), stock=18),
                Product(id="SKU-BAG-001", sku="BAG-001", name="城市通勤双肩包", description="防泼水通勤背包", price=Decimal("269.00"), stock=25),
            ])
        order = await db.get(Order, "ORD-2026-0001")
        if order is None:
            db.add(Order(
                id="ORD-2026-0001", user_id=settings.demo_user_id, status="shipped",
                carrier="顺丰速运", tracking_no="SF1234567890", logistics_status="运输中，预计明日送达",
                total_amount=Decimal("399.00"), refunded_amount=Decimal("0.00"),
            ))
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_demo_data()
    provider = create_provider(settings)
    memory = create_memory_adapter(settings)
    governance = create_governance_adapter(settings)
    app.state.supervisor = AgentSupervisor(
        settings=settings, provider=provider, memory=memory, governance=governance,
        registry=build_registry(), n8n=N8NClient(settings),
    )
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)
app.include_router(router, prefix=settings.api_prefix)

# ---------------------------------------------------------------- realtime UI
from pathlib import Path as _Path  # noqa: E402

from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

_WEB_DIR = _Path(__file__).resolve().parents[1] / "web"
if _WEB_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_WEB_DIR), html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def _ui_redirect() -> RedirectResponse:
        return RedirectResponse(url="/ui/")
