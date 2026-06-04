"""Zone heatmap: popularity + average dwell, normalised to a 0-100 score.

A low-confidence flag is raised when the total observed sessions for the store
fall below 20, signalling that scores are statistically weak.
"""
from __future__ import annotations

import sqlite3

MIN_CONFIDENT_SESSIONS = 20


def compute_heatmap(conn: sqlite3.Connection, store_id: str) -> dict:
    rows = conn.execute(
        """
        SELECT zone_id,
               COUNT(DISTINCT visitor_id) AS sessions,
               AVG(dwell_ms)              AS avg_dwell
        FROM events
        WHERE store_id = ? AND is_staff = 0 AND zone_id IS NOT NULL
              AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL', 'ZONE_EXIT')
        GROUP BY zone_id
        """,
        (store_id,),
    ).fetchall()

    zones = []
    total_sessions = 0
    # Score is normalised by the busiest zone (sessions * avg_dwell weight).
    weights = []
    for r in rows:
        avg_dwell = r["avg_dwell"] or 0.0
        weight = (r["sessions"] or 0) * (avg_dwell or 1)
        weights.append(weight)
        total_sessions += r["sessions"] or 0

    max_weight = max(weights) if weights else 0

    for r, weight in zip(rows, weights):
        score = round((weight / max_weight) * 100, 1) if max_weight else 0.0
        zones.append(
            {
                "zone_id": r["zone_id"],
                "sessions": r["sessions"] or 0,
                "avg_dwell_ms": round(r["avg_dwell"], 2) if r["avg_dwell"] else 0.0,
                "heatmap_score": score,
            }
        )

    zones.sort(key=lambda z: z["heatmap_score"], reverse=True)
    return {
        "store_id": store_id,
        "zones": zones,
        "total_sessions": total_sessions,
        "low_confidence": total_sessions < MIN_CONFIDENT_SESSIONS,
    }
