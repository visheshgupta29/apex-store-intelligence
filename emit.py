"""emit.py — Normalise the detection layer's output into canonical events.

The provided `sample_events.jsonl` uses several heterogeneous record shapes
(entry/exit, zone_entered/exited, queue_completed/abandoned) with inconsistent
field names and store ids. This module is the adapter between that raw feed and
the canonical event schema the Intelligence API ingests. It also DERIVES events
the raw feed only implies:

  * ZONE_DWELL            when a zone visit lasts > 30s
  * REENTRY               when a visitor enters again after a prior exit
  * BILLING_QUEUE_JOIN    from any queue record (visitor was present in billing)
  * BILLING_QUEUE_ABANDON when a queue record is flagged abandoned

Usage:
    python emit.py                 # read default sample file, POST to API
    python emit.py --dry-run       # print canonical events, do not POST
    python emit.py --file path.jsonl --api http://localhost:8000
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime
from typing import Any, Iterable

DEFAULT_SAMPLE = os.environ.get(
    "SAMPLE_EVENTS",
    os.path.join(os.path.dirname(__file__), "..", "data", "sample_eventsbe42122.jsonl"),
)
DEFAULT_API = os.environ.get("API_URL", "http://localhost:8000")
DWELL_THRESHOLD_MS = 30_000
DEFAULT_CONFIDENCE = 0.9


def _norm_store(raw: str) -> str:
    if not raw:
        return "ST_UNKNOWN"
    s = str(raw).strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    low = s.lower()
    if (low.startswith("store_") or "purplle" in low) and digits:
        return f"ST{digits}"
    return s


def _iso(ts: str | None) -> str | None:
    if not ts:
        return None
    ts = ts.strip()
    return ts if ts.endswith("Z") or "+" in ts else ts + "Z"


def _to_dt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _event(store, event_type, visitor, ts, **kw) -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store,
        "camera_id": kw.get("camera_id"),
        "visitor_id": visitor,
        "event_type": event_type,
        "timestamp": _iso(ts),
        "zone_id": kw.get("zone_id"),
        "dwell_ms": kw.get("dwell_ms"),
        "is_staff": bool(kw.get("is_staff", False)),
        "confidence": kw.get("confidence", DEFAULT_CONFIDENCE),
        "metadata": {
            "queue_depth": kw.get("queue_depth"),
            "sku_zone": kw.get("sku_zone"),
            "session_seq": kw.get("session_seq"),
        },
    }


def normalize(records: Iterable[dict]) -> list[dict]:
    """Convert raw heterogeneous records into a list of canonical events."""
    canonical: list[dict] = []
    seen_entries: dict[str, int] = {}          # visitor -> entry count (for REENTRY)
    zone_open: dict[tuple, str] = {}           # (visitor, zone) -> enter ts

    for rec in records:
        et = (rec.get("event_type") or "").lower()

        # --- Entry / Exit feed (id_token namespace) ---
        if et in ("entry", "exit"):
            store = _norm_store(rec.get("store_code") or rec.get("store_id"))
            visitor = rec.get("id_token")
            ts = rec.get("event_timestamp")
            is_staff = bool(rec.get("is_staff", False))
            if et == "entry":
                seen_entries[visitor] = seen_entries.get(visitor, 0) + 1
                etype = "REENTRY" if seen_entries[visitor] > 1 else "ENTRY"
                canonical.append(
                    _event(store, etype, visitor, ts,
                           camera_id=rec.get("camera_id"), is_staff=is_staff,
                           session_seq=seen_entries[visitor])
                )
            else:
                canonical.append(
                    _event(store, "EXIT", visitor, ts,
                           camera_id=rec.get("camera_id"), is_staff=is_staff)
                )

        # --- Zone feed (track_id namespace) ---
        elif et in ("zone_entered", "zone_exited"):
            store = _norm_store(rec.get("store_id"))
            visitor = f"T{rec.get('track_id')}"
            zone = rec.get("zone_id")
            ts = rec.get("event_time")
            if et == "zone_entered":
                zone_open[(visitor, zone)] = ts
                canonical.append(
                    _event(store, "ZONE_ENTER", visitor, ts,
                           camera_id=rec.get("camera_id"), zone_id=zone,
                           sku_zone=rec.get("zone_name"))
                )
            else:
                enter_ts = zone_open.pop((visitor, zone), None)
                canonical.append(
                    _event(store, "ZONE_EXIT", visitor, ts,
                           camera_id=rec.get("camera_id"), zone_id=zone,
                           sku_zone=rec.get("zone_name"))
                )
                start, end = _to_dt(enter_ts), _to_dt(ts)
                if start and end:
                    dwell_ms = int((end - start).total_seconds() * 1000)
                    if dwell_ms > DWELL_THRESHOLD_MS:
                        canonical.append(
                            _event(store, "ZONE_DWELL", visitor, enter_ts,
                                   camera_id=rec.get("camera_id"), zone_id=zone,
                                   dwell_ms=dwell_ms, sku_zone=rec.get("zone_name"))
                        )

        # --- Billing queue feed (track_id namespace) ---
        elif et in ("queue_completed", "queue_abandoned"):
            store = _norm_store(rec.get("store_id"))
            visitor = f"T{rec.get('track_id')}"
            zone = rec.get("zone_id")
            depth = rec.get("queue_position_at_join")
            canonical.append(
                _event(store, "BILLING_QUEUE_JOIN", visitor, rec.get("queue_join_ts"),
                       camera_id=rec.get("camera_id"), zone_id=zone, queue_depth=depth)
            )
            if rec.get("abandoned"):
                canonical.append(
                    _event(store, "BILLING_QUEUE_ABANDON", visitor, rec.get("queue_exit_ts"),
                           camera_id=rec.get("camera_id"), zone_id=zone, queue_depth=depth)
                )

    # Stable ordering by timestamp for realistic replay.
    canonical.sort(key=lambda e: e["timestamp"] or "")
    return canonical


def load_records(path: str) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def post_events(events: list[dict], api: str, batch: int = 500) -> None:
    import httpx

    for i in range(0, len(events), batch):
        chunk = events[i : i + batch]
        resp = httpx.post(f"{api}/events/ingest", json={"events": chunk}, timeout=30)
        print(f"POST /events/ingest [{i}:{i+len(chunk)}] -> {resp.status_code} {resp.text}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Normalise + emit canonical events.")
    ap.add_argument("--file", default=DEFAULT_SAMPLE)
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    records = load_records(args.file)
    events = normalize(records)
    print(f"Normalised {len(records)} raw records -> {len(events)} canonical events.")

    if args.dry_run:
        print(json.dumps(events, indent=2))
        return
    post_events(events, args.api)


if __name__ == "__main__":
    main()
