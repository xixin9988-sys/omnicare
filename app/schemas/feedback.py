"""Feedback contract: the user's verdict on one AI answer, bound to a memory trace."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(min_length=4, max_length=80)
    verdict: Literal["up", "down"]
    reason: str | None = Field(default=None, max_length=80)
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackOut(BaseModel):
    feedback_id: str
    trace_id: str
    verdict: str
    forwarded: bool
    created_at: str


__all__ = ["FeedbackIn", "FeedbackOut"]
