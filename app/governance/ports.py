from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class GuardDecision:
    allowed: bool
    decision: str
    reason_code: str
    detail: str = ""
    # Optional policy-rule attribution (the mem-guard rule that fired, if any). Defaults keep
    # the historical four-positional-argument construction working.
    rule_id: str = ""
    rule_label: str = ""
    matched_rules: tuple[str, ...] = ()


class GovernancePort(Protocol):
    def validate_memory(self, memory: Any) -> GuardDecision: ...
    async def authorize_action(
        self, *, action: str, args: dict, context: dict, db: Any
    ) -> GuardDecision: ...
    def evaluate_input(self, text: str) -> GuardDecision: ...
    def evaluate_output(self, text: str) -> GuardDecision: ...
