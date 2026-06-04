"""Pydantic models for the canonical event schema and API responses."""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator

# Canonical event types accepted by the platform.
VALID_EVENT_TYPES = {
    "ENTRY",
    "EXIT",
    "ZONE_ENTER",
    "ZONE_EXIT",
    "ZONE_DWELL",
    "BILLING_QUEUE_JOIN",
    "BILLING_QUEUE_ABANDON",
    "REENTRY",
}


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None

    model_config = {"extra": "allow"}


class Event(BaseModel):
    """A single behavioural event emitted by the detection layer."""

    event_id: str
    store_id: str
    camera_id: Optional[str] = None
    visitor_id: Optional[str] = None
    event_type: str
    timestamp: str
    zone_id: Optional[str] = None
    dwell_ms: Optional[int] = None
    is_staff: bool = False
    confidence: Optional[float] = None
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, v: str) -> str:
        up = v.strip().upper()
        if up not in VALID_EVENT_TYPES:
            raise ValueError(f"unknown event_type: {v}")
        return up


class IngestRequest(BaseModel):
    events: List[dict[str, Any]] = Field(default_factory=list)


class IngestRejection(BaseModel):
    index: int
    event_id: Optional[str] = None
    reason: str


class IngestResponse(BaseModel):
    received: int
    accepted: int
    duplicates: int
    rejected: int
    rejections: List[IngestRejection] = Field(default_factory=list)
