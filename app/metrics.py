"""Store metrics + the North Star conversion calculation.

Conversion rule (per challenge spec): a visitor is 'converted' if they were
present in the billing zone within 5 minutes BEFORE a POS transaction timestamp.
No customer identifiers exist, so each transaction is greedily matched to at most
one as-yet-unmatched billing visitor.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional

CONVERSION_WINDOW = timedelta(minutes=5)


def parse_ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    # Work in naive UTC for simple comparisons.
    return dt.replace(tzinfo=None)


def unique_visitors(conn: sqlite3.Connection, store_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(DISTINCT visitor_id) AS n FROM events "
        "WHERE store_id = ? AND is_staff = 0 AND visitor_id IS NOT NULL",
        (store_id,),
    ).fetchone()
    return row["n"] or 0


def converted_visitors(conn: sqlite3.Connection, store_id: str) -> set[str]:
    """Return the set of visitor_ids matched to a transaction within the window."""
    billing = conn.execute(
        "SELECT visitor_id, timestamp FROM events "
        "WHERE store_id = ? AND is_staff = 0 AND visitor_id IS NOT NULL "
        "AND event_type = 'BILLING_QUEUE_JOIN'",
        (store_id,),
    ).fetchall()

    # earliest billing presence per visitor
    presence: dict[str, datetime] = {}
    for r in billing:
        ts = parse_ts(r["timestamp"])
        if ts is None:
            continue
        vid = r["visitor_id"]
        if vid not in presence or ts < presence[vid]:
            presence[vid] = ts

    txns = conn.execute(
        "SELECT timestamp FROM transactions WHERE store_id = ? ORDER BY timestamp",
        (store_id,),
    ).fetchall()

    converted: set[str] = set()
    available = sorted(presence.items(), key=lambda kv: kv[1])
    for t in txns:
        txn_time = parse_ts(t["timestamp"])
        if txn_time is None:
            continue
        for vid, p_time in available:
            if vid in converted:
                continue
            if txn_time - CONVERSION_WINDOW <= p_time <= txn_time:
                converted.add(vid)
                break
    return converted


def avg_dwell_ms(conn: sqlite3.Connection, store_id: str) -> float:
    row = conn.execute(
        "SELECT AVG(dwell_ms) AS d FROM events "
        "WHERE store_id = ? AND is_staff = 0 AND event_type = 'ZONE_DWELL' "
        "AND dwell_ms IS NOT NULL",
        (store_id,),
    ).fetchone()
    return round(row["d"], 2) if row["d"] is not None else 0.0


def current_queue_depth(conn: sqlite3.Connection, store_id: str) -> int:
    row = conn.execute(
        "SELECT MAX(queue_depth) AS q FROM events "
        "WHERE store_id = ? AND queue_depth IS NOT NULL",
        (store_id,),
    ).fetchone()
    return row["q"] or 0


def abandonment_rate(conn: sqlite3.Connection, store_id: str) -> float:
    joins = conn.execute(
        "SELECT COUNT(DISTINCT visitor_id) AS n FROM events "
        "WHERE store_id = ? AND is_staff = 0 AND event_type = 'BILLING_QUEUE_JOIN'",
        (store_id,),
    ).fetchone()["n"] or 0
    abandons = conn.execute(
        "SELECT COUNT(DISTINCT visitor_id) AS n FROM events "
        "WHERE store_id = ? AND is_staff = 0 AND event_type = 'BILLING_QUEUE_ABANDON'",
        (store_id,),
    ).fetchone()["n"] or 0
    if joins == 0:
        return 0.0
    return round(abandons / joins, 4)


def compute_metrics(conn: sqlite3.Connection, store_id: str) -> dict:
    visitors = unique_visitors(conn, store_id)
    converted = converted_visitors(conn, store_id)
    conversion = round(len(converted) / visitors, 4) if visitors else 0.0
    return {
        "store_id": store_id,
        "unique_visitors": visitors,
        "converted_visitors": len(converted),
        "conversion_rate": conversion,
        "avg_dwell_ms": avg_dwell_ms(conn, store_id),
        "queue_depth": current_queue_depth(conn, store_id),
        "abandonment_rate": abandonment_rate(conn, store_id),
    }
