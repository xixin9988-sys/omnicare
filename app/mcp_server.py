"""Standalone MCP server for read-only and guarded e-commerce tools.

Run: python -m app.mcp_server
Endpoint: http://127.0.0.1:9000/mcp (Streamable HTTP)
"""

from mcp.server import MCPServer
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal, init_db
from app.models.entities import Order, Product

mcp = MCPServer("OmniCare Business Tools")


@mcp.tool()
async def search_products(query: str) -> list[dict]:
    """Search active products by name keyword. Read-only."""
    await init_db()
    async with SessionLocal() as db:
        rows = (
            await db.scalars(
                select(Product)
                .where(Product.active.is_(True), Product.name.contains(query))
                .limit(8)
            )
        ).all()
        return [
            {"id": x.id, "sku": x.sku, "name": x.name, "price": str(x.price), "stock": x.stock}
            for x in rows
        ]


@mcp.tool()
async def check_logistics(order_id: str) -> dict:
    """Check logistics for the configured demo user. Production must derive tenant/user identity from authenticated host context."""
    await init_db()
    effective_user = get_settings().demo_user_id
    async with SessionLocal() as db:
        order = await db.scalar(
            select(Order).where(Order.id == order_id, Order.user_id == effective_user)
        )
        if not order:
            return {"ok": False, "error": "order_not_found_or_not_owned"}
        return {
            "ok": True,
            "order_id": order.id,
            "carrier": order.carrier,
            "tracking_no": order.tracking_no,
            "status": order.logistics_status,
        }


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=9000)
