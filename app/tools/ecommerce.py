from decimal import Decimal
from pydantic import BaseModel, Field
from sqlalchemy import select, desc
from app.models.entities import Order, Product, RefundRequest
from app.tools.registry import ToolContext, ToolResult, ToolSpec, Permission, ToolRegistry


class LogisticsArgs(BaseModel):
    order_id: str | None = Field(default=None, max_length=64)


class ProductSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=120)


class RefundArgs(BaseModel):
    order_id: str = Field(min_length=1, max_length=64)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reason: str = Field(default="用户申请退款", max_length=500)


async def get_logistics(args: LogisticsArgs, ctx: ToolContext) -> ToolResult:
    stmt = select(Order).where(Order.user_id == ctx.user_id)
    if args.order_id:
        stmt = stmt.where(Order.id == args.order_id)
    stmt = stmt.order_by(desc(Order.created_at)).limit(1)
    order = await ctx.db.scalar(stmt)
    if not order:
        return ToolResult(False, {}, "order_not_found", "没有找到属于你的订单。")
    return ToolResult(True, {
        "order_id": order.id, "status": order.status, "carrier": order.carrier,
        "tracking_no": order.tracking_no, "logistics_status": order.logistics_status,
        "created_at": order.created_at.isoformat(),
    }, message="物流查询成功")


async def search_products(args: ProductSearchArgs, ctx: ToolContext) -> ToolResult:
    stmt = select(Product).where(Product.active.is_(True), Product.name.contains(args.query)).limit(8)
    rows = list((await ctx.db.scalars(stmt)).all())
    return ToolResult(True, {"items": [
        {"id": p.id, "sku": p.sku, "name": p.name, "price": str(p.price), "stock": p.stock}
        for p in rows
    ]}, message=f"找到 {len(rows)} 件商品")


async def request_refund(args: RefundArgs, ctx: ToolContext) -> ToolResult:
    order = await ctx.db.scalar(select(Order).where(Order.id == args.order_id, Order.user_id == ctx.user_id))
    if not order:
        return ToolResult(False, {}, "order_not_found", "没有找到属于你的订单。")
    refund = RefundRequest(
        order_id=order.id, user_id=ctx.user_id, amount=args.amount,
        reason=args.reason, status="pending_review",
    )
    ctx.db.add(refund)
    await ctx.db.flush()
    return ToolResult(True, {
        "refund_request_id": refund.id,
        "order_id": order.id,
        "amount": str(refund.amount),
        "status": refund.status,
    }, message="退款申请已提交审核；这不代表退款已经到账。")


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="get_logistics", description="查询当前用户订单物流。未传订单号时查询最近一笔订单。",
        args_schema=LogisticsArgs, permission=Permission.READ_ONLY, timeout_seconds=4,
        handler=get_logistics,
    ))
    registry.register(ToolSpec(
        name="search_products", description="按商品名称关键词搜索在售商品。",
        args_schema=ProductSearchArgs, permission=Permission.READ_ONLY, timeout_seconds=4,
        handler=search_products,
    ))
    registry.register(ToolSpec(
        name="request_refund", description="为当前用户订单创建待审核退款申请；执行前必须经过治理授权。",
        args_schema=RefundArgs, permission=Permission.BUSINESS_WRITE, timeout_seconds=5,
        handler=request_refund,
    ))
    return registry
