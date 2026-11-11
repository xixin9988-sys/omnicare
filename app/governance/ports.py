from dataclasses import dataclass
from typing import Protocol, Any


@dataclass(slots=True)
class GuardDecision:
    allowed: bool
    decision: str
    reason_code: str
    detail: str = ""


class GovernancePort(Protocol):
    def validate_memory(self, memory: Any) -> GuardDecision: ...
    async def authorize_action(self, *, action: str, args: dict, context: dict, db: Any) -> GuardDecision: ...
    def evaluate_input(self, text: str) -> GuardDecision: ...
    def evaluate_output(self, text: str) -> GuardDecision: ...
