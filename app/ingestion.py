"""Event ingestion: validation, deduplication, idempotency, partial success."""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from .db import get_conn, init_db, normalize_store_id
from .models import Event, IngestRejection, IngestResponse

MAX_BATCH = 500


def ingest_events(raw_events: list[dict[str, Any]]) -> IngestResponse:
    """Validate and persist a batch of events.

    - Rejects invalid events individually (partial success).
    - Idempotent: re-posting the same event_id is a no-op (counted as duplicate).
    """
    received = len(raw_events)
    if received > MAX_BATCH:
        raise ValueError(f"batch too large: {received} > {MAX_BATCH}")

    valid: list[Event] = []
    rejections: list[IngestRejection] = []

    for idx, item in enumerate(raw_events):
        try:
            event = Event.model_validate(item)
            valid.append(event)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            rejections.append(
                IngestRejection(
                    index=idx,
                    event_id=item.get("event_id") if isinstance(item, dict) else None,
                    reason=str(first.get("msg", "validation error")),
                )
            )

    init_db()
    accepted = 0
    duplicates = 0
    with get_conn() as conn:
        for event in valid:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    event_id, store_id, visitor_id, camera_id, event_type,
                    zone_id, timestamp, dwell_ms, is_staff, confidence,
                    queue_depth, session_seq, raw
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    normalize_store_id(event.store_id),
                    event.visitor_id,
                    event.camera_id,
                    event.event_type,
                    event.zone_id,
                    event.timestamp,
                    event.dwell_ms,
                    1 if event.is_staff else 0,
                    event.confidence,
                    event.metadata.queue_depth,
                    event.metadata.session_seq,
                    json.dumps(event.model_dump()),
                ),
            )
            if cur.rowcount == 1:
                accepted += 1
            else:
                duplicates += 1

    return IngestResponse(
        received=received,
        accepted=accepted,
        duplicates=duplicates,
        rejected=len(rejections),
        rejections=rejections,
    )
