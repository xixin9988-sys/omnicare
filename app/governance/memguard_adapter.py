"""Governance adapter backed by the real **mem-guard** SDK.

mem-guard supplies the rule engine and trust vocabulary; business authorization that needs the
database (refund limits, order ownership) stays in :class:`LocalPolicyGuard`, because mem-guard
is a governance layer, not a payment system. The composition is explicit: mem-guard decides
*whether the statement/action is allowed by policy*, the business guard decides *whether the
business fact permits it*.
"""

from __future__ import annotations

from typing import Any

from app.governance.ports import GuardDecision

try:  # the SDK is optional at import time so the local backend still boots without it
    from mem_guard import Guard
    from mem_guard.consolidation import GateAction  # noqa: F401
    from mem_guard.models import TrustLevel, trust_rank
    from mem_guard.rules import Rule, RuleAction
except ImportError:  # pragma: no cover - handled by the factory message
    Guard = None  # type: ignore[assignment]


_TRUST_ALIASES = {
    "user_stated": "user_stated",
    "user_provided": "user_stated",
    "user_verified": "user_verified",
    "verified": "user_verified",
    "model_inferred": "model_inferred",
    "untrusted": "untrusted",
    "unknown": "untrusted",
}


def _trust(value: str) -> str:
    return _TRUST_ALIASES.get((value or "unknown").lower(), "untrusted")


class MemGuardAdapter:
    """Implements :class:`GovernancePort` using mem-guard for policy decisions."""

    def __init__(self, policy_version: str = "omnicare-memguard-1") -> None:
        if Guard is None:  # pragma: no cover
            raise RuntimeError(
                "mem-guard is not importable; install mem-guard or set MEM_GUARD_PATH"
            )
        self.policy_version = policy_version
        self.guard = Guard()
        self._install_rules()

    def _install_rules(self) -> None:
        rules = [
            Rule(
                id="no-prompt-injection",
                name="Reject prompt injection",
                action=RuleAction.DENY,
                scope="chat",
                trigger=r"ignore\s+(all\s+)?(previous|prior)\s+instructions|reveal\s+the\s+system\s+prompt",
                priority=90,
                rationale="never let user text rewrite system rules",
            ),
            Rule(
                id="no-abuse",
                name="Reject abusive language",
                action=RuleAction.DENY,
                scope="chat",
                trigger=r"(idiot|stupid|trash|garbage)",
                priority=60,
                rationale="keep support conversations civil",
            ),
            Rule(
                id="no-refund-completion-claim",
                name="Do not claim a refund was completed",
                action=RuleAction.DENY,
                scope="output",
                trigger=r"refund (has been )?(completed|paid)|已退款|退款已到账",
                priority=90,
                rationale="this system only creates a pending_review request",
            ),
        ]
        for rule in rules:
            self.guard.add_rule(rule)

    # ------------------------------------------------------------------ memory
    def validate_memory(self, memory: Any) -> GuardDecision:
        state = str(getattr(memory, "state", "unknown") or "unknown").upper()
        trust = _trust(str(getattr(memory, "trust_level", "unknown") or "unknown"))
        if state != "ACTIVE":
            return GuardDecision(False, "quarantine", "memory_state_not_active", state)
        if trust_rank(TrustLevel(trust)) < trust_rank(TrustLevel.USER_STATED):
            return GuardDecision(False, "quarantine", "memory_trust_below_user_stated", trust)
        return GuardDecision(True, "allow", "memory_accepted")

    # ------------------------------------------------------------------ text
    def evaluate_input(self, text: str) -> GuardDecision:
        decision = self.guard.evaluate_rule(text, scope="chat")
        if decision.action is RuleAction.DENY:
            return GuardDecision(
                False, "reject", decision.matched[0] if decision.matched else "input_denied"
            )
        if decision.human_required:
            return GuardDecision(False, "quarantine", "rule_conflict_requires_human")
        return GuardDecision(True, "allow", "input_accepted")

    def evaluate_output(self, text: str) -> GuardDecision:
        decision = self.guard.evaluate_rule(text, scope="output")
        if decision.action is RuleAction.DENY:
            return GuardDecision(
                False,
                "quarantine",
                decision.matched[0] if decision.matched else "output_denied",
            )
        return GuardDecision(True, "allow", "output_accepted")

    # ------------------------------------------------------------------ actions
    async def authorize_action(
        self, *, action: str, args: dict, context: dict, db: Any
    ) -> GuardDecision:
        # Lazy import keeps this module importable without the database stack.
        from app.governance.local_guard import LocalPolicyGuard

        business = await LocalPolicyGuard(self.policy_version).authorize_action(
            action=action, args=args, context=context, db=db
        )
        if not business.allowed:
            return business
        decision = self.guard.evaluate_rule(action, scope="action")
        if decision.action is RuleAction.DENY:
            return GuardDecision(False, "reject", "action_denied_by_rule")
        return GuardDecision(True, "allow", "action_allowed")


__all__ = ["MemGuardAdapter"]
