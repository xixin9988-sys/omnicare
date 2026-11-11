from app.agents.router import Route
from app.tools.ecommerce import ProductSearchArgs
from app.tools.registry import ToolContext, ToolRegistry


class ShoppingAgent:
    name = "shopping_agent"

    async def handle(self, route: Route, context: ToolContext, registry: ToolRegistry) -> str:
        result = await registry.execute(
            "search_products",
            ProductSearchArgs(query=route.product_query or "商品").model_dump(),
            context,
        )
        if not result.success:
            return result.message or "暂时无法查询商品。"
        items = result.data["items"]
        if not items:
            return "暂时没有找到匹配的在售商品。你可以告诉我更具体的商品名称或型号。"
        return "为你找到以下商品：\n" + "\n".join(
            f"- {p['name']}（SKU {p['sku']}），¥{p['price']}，库存 {p['stock']} 件" for p in items
        )
