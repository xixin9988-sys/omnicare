import re
from decimal import Decimal

from sqlalchemy import desc, select

from app.agents.router import Route
from app.models.entities import Order
from app.tools.ecommerce import RefundArgs
from app.tools.registry import ToolContext, ToolRegistry


class AfterSalesAgent:
    name = "aftersales_agent"

    async def handle(
        self, text: str, route: Route, context: ToolContext, registry: ToolRegistry
    ) -> str:
        order = None
        if route.order_id:
            order = await context.db.scalar(
                select(Order).where(Order.id == route.order_id, Order.user_id == context.user_id)
            )
        else:
            order = await context.db.scalar(
                select(Order)
                .where(Order.user_id == context.user_id)
                .order_by(desc(Order.created_at))
                .limit(1)
            )
        if not order:
            return "暂时没有找到属于你的订单。请提供订单号，我再帮你核对退款条件。"
        amount_match = re.search(
            r"(?:退款|退)\s*(?:金额)?\s*[¥￥]?\s*([0-9]+(?:\.[0-9]{1,2})?)", text
        )
        if amount_match is None:
            refundable = Decimal(order.total_amount) - Decimal(order.refunded_amount)
            return (
                f"已找到订单 {order.id}。当前可申请退款上限为 ¥{refundable:.2f}，"
                "请告诉我你希望申请的具体金额；我不会默认替你申请全部金额。"
            )
        amount = Decimal(amount_match.group(1))
        result = await registry.execute(
            "request_refund",
            RefundArgs(
                order_id=order.id, amount=amount, reason="用户通过 OmniCare 客服申请退款"
            ).model_dump(mode="json"),
            context,
        )
        if not result.success:
            detail = result.data.get("detail")
            return f"这笔退款申请未能提交：{detail or result.message or '未通过业务与安全校验'}"
        return (
            f"已为订单 {order.id} 创建退款审核申请，金额 ¥{amount:.2f}，"
            f"申请编号 {result.data['refund_request_id']}。当前状态为待审核，并非退款已到账。"
        )
