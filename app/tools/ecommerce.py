from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError

from app.models.entities import Order, Product, RefundRequest
from app.services.refund_state import OPEN_STATUSES, refund_fingerprint
from app.services.refunds import available_refund, lock_clause, open_refund_total
from app.tools.registry import Permission, ToolContext, ToolRegistry, ToolResult, ToolSpec


class LogisticsArgs(BaseModel):
    order_id: str | None = Field(default=None, max_length=64)


class ProductSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=120)


class RefundArgs(BaseModel):
    order_id: str = Field(min_length=1, max_length=64)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reason: str = Field(default="用户申请退款", max_length=500)
    idempotency_key: str | None = Field(default=None, max_length=80)


async def get_logistics(args: LogisticsArgs, ctx: ToolContext) -> ToolResult:
    stmt = select(Order).where(Order.user_id == ctx.user_id)
    if args.order_id:
        stmt = stmt.where(Order.id == args.order_id)
    stmt = stmt.order_by(desc(Order.created_at)).limit(1)
    order = await ctx.db.scalar(stmt)
    if not order:
        return ToolResult(False, {}, "order_not_found", "没有找到属于你的订单。")
    return ToolResult(
        True,
        {
            "order_id": order.id,
            "status": order.status,
            "carrier": order.carrier,
            "tracking_no": order.tracking_no,
            "logistics_status": order.logistics_status,
            "created_at": order.created_at.isoformat(),
        },
        message="物流查询成功",
    )


async def search_products(args: ProductSearchArgs, ctx: ToolContext) -> ToolResult:
    stmt = (
        select(Product).where(Product.active.is_(True), Product.name.contains(args.query)).limit(8)
    )
    rows = list((await ctx.db.scalars(stmt)).all())
    return ToolResult(
        True,
        {
            "items": [
                {"id": p.id, "sku": p.sku, "name": p.name, "price": str(p.price), "stock": p.stock}
                for p in rows
            ]
        },
        message=f"找到 {len(rows)} 件商品",
    )


async def request_refund(args: RefundArgs, ctx: ToolContext) -> ToolResult:
    # Lock the order so two concurrent requests cannot both pass the balance check.
    order_stmt = select(Order).where(Order.id == args.order_id, Order.user_id == ctx.user_id)
    if lock_clause(ctx.db):
        order_stmt = order_stmt.with_for_update()
    order = await ctx.db.scalar(order_stmt)
    if not order:
        return ToolResult(False, {}, "order_not_found", "没有找到属于你的订单。")

    # The dedupe key is derived by the server from (order, amount, reason): a retry that
    # forgets to send idempotency_key still collapses onto the existing open request.
    fingerprint = refund_fingerprint(order.id, args.amount, args.reason)
    existing = await _open_request_for(ctx.db, order.id, fingerprint)
    if existing is None and args.idempotency_key:
        existing = await ctx.db.scalar(
            select(RefundRequest).where(
                RefundRequest.idempotency_key == args.idempotency_key,
                RefundRequest.user_id == ctx.user_id,
            )
        )
    if existing is not None:
        return _duplicate_result(existing)

    available = await available_refund(ctx.db, order)
    requested = args.amount
    open_total = await open_refund_total(ctx.db, order.id)
    if requested > available:
        return ToolResult(
            False,
            {
                "order_id": order.id,
                "requested": str(requested),
                "available": str(available),
                "open_requests": str(open_total),
            },
            "refund_exceeds_authoritative_limit",
            f"可退余额不足：可退 {available:.2f}，已有待审核/已批准 {open_total:.2f}。",
        )

    refund = RefundRequest(
        order_id=order.id,
        user_id=ctx.user_id,
        amount=requested,
        reason=args.reason,
        status="pending_review",
        fingerprint=fingerprint,
        idempotency_key=args.idempotency_key,
    )
    try:
        # Savepoint: a unique-index hit must not poison the caller's session.
        async with ctx.db.begin_nested():
            ctx.db.add(refund)
            await ctx.db.flush()
    except IntegrityError:
        existing = await _open_request_for(ctx.db, order.id, fingerprint)
        if existing is not None:
            return _duplicate_result(existing)
        return ToolResult(False, {}, "refund_conflict", "退款申请冲突，请稍后重试。")
    return ToolResult(
        True,
        {
            "refund_request_id": refund.id,
            "order_id": order.id,
            "amount": str(refund.amount),
            "status": refund.status,
        },
        message="退款申请已提交审核；这不代表退款已经到账。",
    )


async def _open_request_for(db, order_id: str, fingerprint: str) -> RefundRequest | None:
    return await db.scalar(
        select(RefundRequest).where(
            RefundRequest.order_id == order_id,
            RefundRequest.fingerprint == fingerprint,
            RefundRequest.status.in_(OPEN_STATUSES),
        )
    )


def _duplicate_result(existing: RefundRequest) -> ToolResult:
    return ToolResult(
        True,
        {
            "refund_request_id": existing.id,
            "order_id": existing.order_id,
            "amount": str(existing.amount),
            "status": existing.status,
            "duplicate": True,
        },
        message="该退款申请已存在（幂等命中），未重复创建。",
    )


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="get_logistics",
            description="查询当前用户订单物流。未传订单号时查询最近一笔订单。",
            args_schema=LogisticsArgs,
            permission=Permission.READ_ONLY,
            timeout_seconds=4,
            handler=get_logistics,
        )
    )
    registry.register(
        ToolSpec(
            name="search_products",
            description="按商品名称关键词搜索在售商品。",
            args_schema=ProductSearchArgs,
            permission=Permission.READ_ONLY,
            timeout_seconds=4,
            handler=search_products,
        )
    )
    registry.register(
        ToolSpec(
            name="request_refund",
            description="为当前用户订单创建待审核退款申请；执行前必须经过治理授权。",
            args_schema=RefundArgs,
            permission=Permission.BUSINESS_WRITE,
            timeout_seconds=5,
            handler=request_refund,
        )
    )
    return registry
