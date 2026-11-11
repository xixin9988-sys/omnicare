from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RefundDecisionRequest(BaseModel):
    decision: Literal["approve", "reject", "settle"]
    reason: str = Field(default="", max_length=500)


class RefundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: str
    user_id: str
    amount: Decimal
    status: str
    reason: str
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str = ""
    created_at: datetime


class RefundAuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    refund_id: str
    order_id: str
    from_status: str
    to_status: str
    decision: str
    actor: str
    reason: str
    created_at: datetime
