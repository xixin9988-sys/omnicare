from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


class SessionOut(BaseModel):
    session_id: str
    user_id: str
    status: str
    created_at: datetime


class ChatRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=36)
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    run_id: str
    session_id: str
    intent: str
    agent: str
    status: str
    reply: str
    usage: dict[str, Any] = Field(default_factory=dict)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    sku: str
    name: str
    description: str
    price: Decimal
    stock: int


class EventOut(BaseModel):
    id: str
    run_id: str
    session_id: str
    event_type: str
    agent: str
    status: str
    payload: dict[str, Any]
    created_at: datetime
