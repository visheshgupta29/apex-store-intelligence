"""Deterministic, threshold-based anomaly detection.

Three detectors, each emitting INFO / WARN / CRITICAL plus a suggested_action:
  - queue_spike     : billing queue depth crosses operational thresholds
  - conversion_drop : conversion rate below an acceptable floor
  - dead_zone       : zones that attract footfall but no engagement (no dwell)
"""
from __future__ import annotations

import sqlite3

from .metrics import compute_metrics

QUEUE_WARN = 5
QUEUE_CRITICAL = 8
CONVERSION_FLOOR_WARN = 0.10
CONVERSION_FLOOR_CRITICAL = 0.03


def _queue_spike(conn: sqlite3.Connection, store_id: str, metrics: dict) -> list[dict]:
    depth = metrics["queue_depth"]
    if depth >= QUEUE_CRITICAL:
        sev = "CRITICAL"
    elif depth >= QUEUE_WARN:
        sev = "WARN"
    else:
        return []
    return [
        {
            "type": "QUEUE_SPIKE",
            "severity": sev,
            "zone_id": None,
            "value": depth,
            "suggested_action": "Open an additional billing counter to drain the queue.",
        }
    ]


def _conversion_drop(store_id: str, metrics: dict) -> list[dict]:
    # Only meaningful once we have a non-trivial number of visitors.
    if metrics["unique_visitors"] < 5:
        return []
    rate = metrics["conversion_rate"]
    if rate < CONVERSION_FLOOR_CRITICAL:
        sev = "CRITICAL"
    elif rate < CONVERSION_FLOOR_WARN:
        sev = "WARN"
    else:
        return []
    return [
        {
            "type": "CONVERSION_DROP",
            "severity": sev,
            "zone_id": None,
            "value": rate,
            "suggested_action": "Review staffing and active promotions; audit billing friction.",
        }
    ]


def _dead_zones(conn: sqlite3.Connection, store_id: str) -> list[dict]:
    # Zones entered by visitors but with no meaningful dwell engagement.
    rows = conn.execute(
        """
        SELECT zone_id,
               SUM(CASE WHEN event_type = 'ZONE_ENTER' THEN 1 ELSE 0 END) AS enters,
               SUM(CASE WHEN event_type = 'ZONE_DWELL' THEN 1 ELSE 0 END) AS dwells
        FROM events
        WHERE store_id = ? AND is_staff = 0 AND zone_id IS NOT NULL
        GROUP BY zone_id
        """,
        (store_id,),
    ).fetchall()
    out = []
    for r in rows:
        if (r["enters"] or 0) > 0 and (r["dwells"] or 0) == 0:
            out.append(
                {
                    "type": "DEAD_ZONE",
                    "severity": "INFO",
                    "zone_id": r["zone_id"],
                    "value": r["enters"],
                    "suggested_action": (
                        f"Re-merchandise or relocate stock in {r['zone_id']}; "
                        "visitors pass through but do not engage."
                    ),
                }
            )
    return out


def detect_anomalies(conn: sqlite3.Connection, store_id: str) -> dict:
    metrics = compute_metrics(conn, store_id)
    anomalies: list[dict] = []
    anomalies += _queue_spike(conn, store_id, metrics)
    anomalies += _conversion_drop(store_id, metrics)
    anomalies += _dead_zones(conn, store_id)
    return {"store_id": store_id, "count": len(anomalies), "anomalies": anomalies}
