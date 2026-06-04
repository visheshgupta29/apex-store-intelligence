"""Optional demo seeding.

When SEED_ON_STARTUP is truthy, load a bundled batch of canonical events
(`seed/seed_events.json`) so a freshly deployed instance (e.g. a free-tier host
with ephemeral storage) always has data on the dashboard. Timestamps are
optionally rebased so the most recent event is "now", which keeps /health fresh
and makes the live demo realistic.

This is demo-only convenience; production ingestion still flows through
POST /events/ingest.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from .ingestion import ingest_events
from .metrics import parse_ts

SEED_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "seed", "seed_events.json"
)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _rebase(events: list[dict]) -> list[dict]:
    """Shift all timestamps so the latest event lands ~now (preserves spacing)."""
    parsed = [parse_ts(e.get("timestamp", "")) for e in events]
    valid = [p for p in parsed if p is not None]
    if not valid:
        return events
    latest = max(valid)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    offset = now - latest
    for e, p in zip(events, parsed):
        if p is not None:
            e["timestamp"] = (p + offset).strftime("%Y-%m-%dT%H:%M:%SZ")
    return events


def seed_if_requested() -> int:
    """Seed bundled events if SEED_ON_STARTUP is set. Returns events ingested."""
    if not _truthy(os.environ.get("SEED_ON_STARTUP")):
        return 0
    if not os.path.exists(SEED_FILE):
        return 0
    with open(SEED_FILE, encoding="utf-8") as fh:
        events = json.load(fh)
    if _truthy(os.environ.get("SEED_REBASE_TIME", "1")):
        events = _rebase(events)
    result = ingest_events(events)
    print(f'{{"event": "seeded", "accepted": {result.accepted}}}')
    return result.accepted
