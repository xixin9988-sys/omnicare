import re
from dataclasses import dataclass
from enum import StrEnum


class Intent(StrEnum):
    LOGISTICS = "logistics"
    REFUND = "refund"
    PRODUCT = "product"
    GENERAL = "general"


@dataclass(slots=True)
class Route:
    intent: Intent
    agent: str
    order_id: str | None = None
    product_query: str | None = None


class IntentRouter:
    """Rule-first router. It is deterministic for consequential flows; LLM routing can be added as a hint."""

    def route(self, text: str) -> Route:
        order_match = re.search(r"\b(ORD-[A-Z0-9-]+)\b", text, flags=re.IGNORECASE)
        order_id = order_match.group(1).upper() if order_match else None
        if any(word in text for word in ["退款", "退货", "退钱", "退货退款"]):
            return Route(Intent.REFUND, "aftersales_agent", order_id=order_id)
        if any(word in text for word in ["物流", "快递", "到哪", "发货", "催发", "运单", "配送"]):
            return Route(Intent.LOGISTICS, "logistics_agent", order_id=order_id)
        if any(word in text for word in ["商品", "价格", "库存", "推荐", "购买", "多少钱", "鞋", "耳机"]):
            query = text
            for word in ["请问", "帮我", "我想买", "查一下", "搜索", "商品"]:
                query = query.replace(word, " ")
            return Route(Intent.PRODUCT, "shopping_agent", product_query=query.strip()[:120] or text[:120])
        return Route(Intent.GENERAL, "general_agent")
