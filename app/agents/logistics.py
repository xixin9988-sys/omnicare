from app.agents.router import Route
from app.tools.ecommerce import LogisticsArgs
from app.tools.registry import ToolContext, ToolRegistry


class LogisticsAgent:
    name = "logistics_agent"

    async def handle(self, route: Route, context: ToolContext, registry: ToolRegistry) -> str:
        result = await registry.execute(
            "get_logistics", LogisticsArgs(order_id=route.order_id).model_dump(), context
        )
        if not result.success:
            return result.message or "暂时无法查询物流。"
        data = result.data
        return (
            f"订单 {data['order_id']} 当前物流状态：{data['logistics_status']}。"
            f"承运方：{data['carrier']}；运单号：{data['tracking_no']}。"
        )
