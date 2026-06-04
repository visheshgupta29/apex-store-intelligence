"""Session-based conversion funnel with re-entry deduplication.

Stages: Entry -> Zone Visit -> Billing Queue -> Purchase.
Every stage is measured on DISTINCT visitor_id so that re-entries
(a visitor leaving and returning) are counted once.
"""
from __future__ import annotations

import sqlite3

from .metrics import converted_visitors


def _distinct_visitors(conn: sqlite3.Connection, store_id: str, types: tuple) -> set:
    placeholders = ",".join("?" for _ in types)
    rows = conn.execute(
        f"SELECT DISTINCT visitor_id FROM events "
        f"WHERE store_id = ? AND is_staff = 0 AND visitor_id IS NOT NULL "
        f"AND event_type IN ({placeholders})",
        (store_id, *types),
    ).fetchall()
    return {r["visitor_id"] for r in rows}


def compute_funnel(conn: sqlite3.Connection, store_id: str) -> dict:
    entry = _distinct_visitors(conn, store_id, ("ENTRY", "REENTRY"))
    zone = _distinct_visitors(conn, store_id, ("ZONE_ENTER", "ZONE_DWELL"))
    billing = _distinct_visitors(conn, store_id, ("BILLING_QUEUE_JOIN",))
    purchase = converted_visitors(conn, store_id)

    stages = [
        ("entry", len(entry)),
        ("zone_visit", len(zone)),
        ("billing_queue", len(billing)),
        ("purchase", len(purchase)),
    ]

    out = []
    prev_count = None
    for name, count in stages:
        if prev_count is None:
            drop = 0.0
        elif prev_count == 0:
            drop = 0.0
        else:
            drop = round((prev_count - count) / prev_count * 100, 2)
        out.append({"stage": name, "count": count, "drop_off_pct": drop})
        prev_count = count

    overall = (
        round(len(purchase) / len(entry) * 100, 2) if entry else 0.0
    )
    return {
        "store_id": store_id,
        "stages": out,
        "overall_conversion_pct": overall,
    }
