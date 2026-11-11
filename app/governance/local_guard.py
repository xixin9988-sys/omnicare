import re
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.governance.ports import GuardDecision
from app.models.entities import Order
from app.services.refunds import available_refund


class LocalPolicyGuard:
    """Deterministic demo policy. Replace this adapter with the real mem-guard SDK adapter."""

    def __init__(self, policy_version: str = "omnicare-demo-1") -> None:
        self.policy_version = policy_version

    def validate_memory(self, memory) -> GuardDecision:
        state = (getattr(memory, "state", "unknown") or "unknown").upper()
        source = getattr(memory, "source", "unknown") or "unknown"
        trust = (getattr(memory, "trust_level", "unknown") or "unknown").lower()
        if state in {"CONFLICT", "QUARANTINED", "SUPERSEDED", "ARCHIVED"}:
            return GuardDecision(False, "quarantine", "memory_state_not_active", state)
        if source == "unknown" or trust == "unknown":
            return GuardDecision(
                False, "quarantine", "memory_provenance_missing", "来源或信任等级缺失"
            )
        return GuardDecision(True, "allow", "memory_accepted")

    def evaluate_input(self, text: str) -> GuardDecision:
        lowered = text.lower()
        patterns = [
            (
                r"免费送(我|给我|一个|一件)|白送(我|给我)",
                "asset_abuse_attempt",
                "不能绕过企业赠品与促销审批规则。",
            ),
            (
                r"忽略(所有|之前|上面).{0,8}(指令|规则)|reveal (the )?(system prompt|secret)",
                "prompt_injection",
                "检测到要求覆盖系统规则或索取内部信息的指令。",
            ),
            (
                r"(?:傻逼|煞笔|蠢货|脑残|废物|垃圾AI|垃圾模型)",
                "abusive_language",
                "请保持基本沟通礼貌；我会继续协助处理正常的订单与售后问题。",
            ),
            (
                r"(?:不要|不想|取消).{0,10}退款.{0,12}(?:但是|但|却|仍然).{0,10}(?:申请|办理|帮我|给我)|(?:申请|办理|帮我|给我).{0,10}退款.{0,12}(?:但是|但|却|同时).{0,10}(?:不要|不想|取消)",
                "contradictory_refund_intent",
                "检测到退款意图前后矛盾，请明确是否需要提交退款申请。",
            ),
        ]
        for pattern, code, detail in patterns:
            if re.search(pattern, lowered, flags=re.IGNORECASE):
                return GuardDecision(False, "reject", code, detail)
        return GuardDecision(True, "allow", "input_accepted")

    async def authorize_action(
        self, *, action: str, args: dict, context: dict, db
    ) -> GuardDecision:
        if action != "request_refund":
            return GuardDecision(True, "allow", "action_allowed")
        order_id = str(args.get("order_id", ""))
        user_id = str(context.get("user_id", ""))
        try:
            requested = Decimal(str(args.get("amount", "0")))
        except (InvalidOperation, ValueError):
            return GuardDecision(False, "reject", "invalid_refund_amount", "退款金额格式不合法。")
        if requested <= 0:
            return GuardDecision(False, "reject", "invalid_refund_amount", "退款金额必须大于 0。")
        order = await db.scalar(
            select(Order).where(Order.id == order_id, Order.user_id == user_id).with_for_update()
        )
        if order is None:
            return GuardDecision(
                False, "reject", "order_not_owned_or_missing", "订单不存在或不属于当前用户。"
            )
        # Includes pending/approved requests: the same number the tool enforces.
        refundable = await available_refund(db, order)
        if requested > refundable:
            return GuardDecision(
                False,
                "quarantine",
                "refund_exceeds_authoritative_limit",
                f"申请金额超过订单可退上限 {refundable:.2f}。",
            )
        if order.status in {"cancelled", "closed"}:
            return GuardDecision(
                False, "reject", "order_not_refundable", "当前订单状态不允许退款申请。"
            )
        return GuardDecision(True, "allow", "refund_within_authoritative_limit")

    def evaluate_output(self, text: str) -> GuardDecision:
        # A simple anti-false-claim check for the demo. Business write is never described as paid.
        if re.search(r"退款已到账|已经完成退款|钱已退回", text):
            return GuardDecision(
                False,
                "quarantine",
                "unverified_refund_completion_claim",
                "输出声称退款到账，但本系统仅创建审核申请。",
            )
        return GuardDecision(True, "allow", "output_accepted")
