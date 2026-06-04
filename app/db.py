"""SQLite access layer: schema, connections, POS loading, outage simulation.

We use SQLite because it is zero-config, file-backed, ships in the Python stdlib,
and is more than sufficient for the analytical read/write patterns in this
challenge. Swapping to Postgres later only touches this module.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

DB_PATH = os.environ.get("STORE_DB", "store_intel.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    store_id    TEXT NOT NULL,
    visitor_id  TEXT,
    camera_id   TEXT,
    event_type  TEXT NOT NULL,
    zone_id     TEXT,
    timestamp   TEXT NOT NULL,
    dwell_ms    INTEGER,
    is_staff    INTEGER NOT NULL DEFAULT 0,
    confidence  REAL,
    queue_depth INTEGER,
    session_seq INTEGER,
    raw         TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_store_type ON events(store_id, event_type);
CREATE INDEX IF NOT EXISTS idx_events_store_ts   ON events(store_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_visitor    ON events(store_id, visitor_id);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,
    store_id       TEXT NOT NULL,
    timestamp      TEXT NOT NULL,
    basket_value   REAL
);
CREATE INDEX IF NOT EXISTS idx_txn_store_ts ON transactions(store_id, timestamp);
"""


class DatabaseOutage(Exception):
    """Raised to simulate a database outage so the API can return HTTP 503."""


def _outage_enabled() -> bool:
    return os.environ.get("STORE_DB_OUTAGE", "0") == "1"


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection. Raises DatabaseOutage when outage is simulated."""
    if _outage_enabled():
        raise DatabaseOutage("database unavailable")
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    # Bypass the outage guard for startup initialisation.
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def normalize_store_id(raw: str) -> str:
    """Normalise the inconsistent store identifiers found across feeds.

    Examples: 'store_1076' -> 'ST1076', 'PURPLLE_MUM_1076' -> 'ST1076'.
    Canonical ids (e.g. 'STORE_BLR_002', 'ST1008') are returned unchanged.
    """
    if not raw:
        return raw
    s = str(raw).strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    low = s.lower()
    if low.startswith("store_") and digits:
        return f"ST{digits}"
    if "purplle" in low and digits:
        return f"ST{digits}"
    return s


def _parse_pos_timestamp(row: dict) -> Optional[str]:
    """Parse either the canonical or the real Purplle POS schema into ISO UTC."""
    # Canonical schema: timestamp column already ISO-8601.
    if row.get("timestamp"):
        return row["timestamp"].strip()
    # Real schema: order_date=DD-MM-YYYY, order_time=HH:MM:SS.
    date = (row.get("order_date") or "").strip()
    time = (row.get("order_time") or "").strip()
    if date and time:
        for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(f"{date} {time}", fmt).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            except ValueError:
                continue
    return None


def load_pos_csv(path: str) -> int:
    """Load a POS CSV (canonical OR real Purplle schema) into transactions.

    Real schema has one row per line item; we collapse rows that share the same
    (store, timestamp) into a single transaction using the summed amount.
    Returns the number of transactions inserted.
    """
    if not path or not os.path.exists(path):
        return 0

    baskets: dict[tuple[str, str], float] = {}
    explicit_ids: dict[tuple[str, str], str] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            store = normalize_store_id(
                row.get("store_id") or row.get("store_code") or ""
            )
            ts = _parse_pos_timestamp(row)
            if not store or not ts:
                continue
            amount = (
                row.get("basket_value_inr")
                or row.get("total_amount")
                or row.get("amount")
                or "0"
            )
            try:
                amount_f = float(amount)
            except ValueError:
                amount_f = 0.0
            key = (store, ts)
            baskets[key] = baskets.get(key, 0.0) + amount_f
            if row.get("transaction_id"):
                explicit_ids[key] = row["transaction_id"].strip()

    init_db()
    inserted = 0
    with get_conn() as conn:
        for (store, ts), value in baskets.items():
            txn_id = explicit_ids.get((store, ts)) or f"TXN_{store}_{ts}"
            cur = conn.execute(
                "INSERT OR IGNORE INTO transactions "
                "(transaction_id, store_id, timestamp, basket_value) "
                "VALUES (?, ?, ?, ?)",
                (txn_id, store, ts, value),
            )
            inserted += cur.rowcount
    return inserted
