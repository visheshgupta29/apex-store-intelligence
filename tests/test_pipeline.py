"""End-to-end tests for the Store Intelligence API.

# PROMPT:
# "Generate pytest tests for a FastAPI store-analytics service backed by SQLite.
#  Cover the four challenge-required scenarios (empty store, all-staff, zero
#  purchases, re-entry) plus ingestion idempotency, partial success, the
#  conversion correlation rule, anomaly thresholds, and the 503 DB-outage path.
#  Use an isolated temp DB per test session and the canonical event schema."
#
# CHANGES MADE:
# - Pointed STORE_DB at a temp file via an autouse fixture and re-init the schema
#   per test for isolation.
# - Seeded transactions directly to validate the 5-minute conversion window
#   without depending on the (excluded) POS CSV.
# - Asserted partial-success semantics return HTTP 200 with per-event rejections.
# - Verified re-entry is deduplicated in the funnel and emitted as REENTRY.
"""
from __future__ import annotations

import importlib
import os
import uuid
from datetime import datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_file = tmp_path / f"test_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("STORE_DB", str(db_file))
    monkeypatch.setenv("STORE_DB_OUTAGE", "0")

    # Reload modules so they pick up the patched DB path.
    import app.db as db
    importlib.reload(db)
    import app.metrics as metrics
    importlib.reload(metrics)
    import app.ingestion as ingestion
    importlib.reload(ingestion)
    import app.funnel as funnel
    importlib.reload(funnel)
    import app.heatmap as heatmap
    importlib.reload(heatmap)
    import app.anomalies as anomalies
    importlib.reload(anomalies)
    import app.main as main
    importlib.reload(main)

    db.init_db()
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    yield client, db


def _evt(store, etype, visitor, ts, **kw):
    e = {
        "event_id": kw.get("event_id", str(uuid.uuid4())),
        "store_id": store,
        "visitor_id": visitor,
        "camera_id": "CAM1",
        "event_type": etype,
        "timestamp": ts,
        "is_staff": kw.get("is_staff", False),
        "confidence": 0.9,
    }
    if "zone_id" in kw:
        e["zone_id"] = kw["zone_id"]
    if "dwell_ms" in kw:
        e["dwell_ms"] = kw["dwell_ms"]
    if "queue_depth" in kw:
        e["metadata"] = {"queue_depth": kw["queue_depth"]}
    return e


def _ingest(client, events):
    return client.post("/events/ingest", json={"events": events})


# --------------------------------------------------------------------------- #
# Scenario 1: empty store                                                      #
# --------------------------------------------------------------------------- #
def test_empty_store_metrics(fresh_db):
    client, _ = fresh_db
    r = client.get("/stores/ST_EMPTY/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["unique_visitors"] == 0
    assert body["conversion_rate"] == 0.0
    assert body["avg_dwell_ms"] == 0.0


def test_empty_store_funnel_and_heatmap(fresh_db):
    client, _ = fresh_db
    f = client.get("/stores/ST_EMPTY/funnel").json()
    assert all(s["count"] == 0 for s in f["stages"])
    h = client.get("/stores/ST_EMPTY/heatmap").json()
    assert h["low_confidence"] is True


# --------------------------------------------------------------------------- #
# Scenario 2: all staff are excluded                                          #
# --------------------------------------------------------------------------- #
def test_all_staff_excluded(fresh_db):
    client, _ = fresh_db
    base = "2026-03-08T18:10:00Z"
    events = [
        _evt("ST1", "ENTRY", "S1", base, is_staff=True),
        _evt("ST1", "ENTRY", "S2", base, is_staff=True),
    ]
    _ingest(client, events)
    m = client.get("/stores/ST1/metrics").json()
    assert m["unique_visitors"] == 0


# --------------------------------------------------------------------------- #
# Scenario 3: visitors but zero purchases                                     #
# --------------------------------------------------------------------------- #
def test_zero_purchases(fresh_db):
    client, _ = fresh_db
    base = "2026-03-08T18:10:00Z"
    events = [
        _evt("ST1", "ENTRY", "V1", base),
        _evt("ST1", "BILLING_QUEUE_JOIN", "V1", "2026-03-08T18:20:00Z"),
    ]
    _ingest(client, events)
    # No transactions seeded -> nobody converts.
    m = client.get("/stores/ST1/metrics").json()
    assert m["unique_visitors"] == 1
    assert m["converted_visitors"] == 0
    assert m["conversion_rate"] == 0.0


# --------------------------------------------------------------------------- #
# Scenario 4: re-entry is deduplicated in the funnel                          #
# --------------------------------------------------------------------------- #
def test_reentry_dedup(fresh_db):
    client, _ = fresh_db
    events = [
        _evt("ST1", "ENTRY", "V1", "2026-03-08T18:10:00Z"),
        _evt("ST1", "EXIT", "V1", "2026-03-08T18:15:00Z"),
        _evt("ST1", "REENTRY", "V1", "2026-03-08T18:30:00Z"),
    ]
    _ingest(client, events)
    f = client.get("/stores/ST1/funnel").json()
    entry_stage = next(s for s in f["stages"] if s["stage"] == "entry")
    assert entry_stage["count"] == 1  # same visitor, counted once
    m = client.get("/stores/ST1/metrics").json()
    assert m["unique_visitors"] == 1


# --------------------------------------------------------------------------- #
# Ingestion: idempotency + partial success                                    #
# --------------------------------------------------------------------------- #
def test_idempotent_ingestion(fresh_db):
    client, _ = fresh_db
    eid = str(uuid.uuid4())
    e = _evt("ST1", "ENTRY", "V1", "2026-03-08T18:10:00Z", event_id=eid)
    r1 = _ingest(client, [e]).json()
    r2 = _ingest(client, [e]).json()
    assert r1["accepted"] == 1
    assert r2["accepted"] == 0
    assert r2["duplicates"] == 1


def test_partial_success(fresh_db):
    client, _ = fresh_db
    good = _evt("ST1", "ENTRY", "V1", "2026-03-08T18:10:00Z")
    bad = {"event_id": str(uuid.uuid4()), "store_id": "ST1", "event_type": "NONSENSE",
           "timestamp": "2026-03-08T18:10:00Z"}
    r = _ingest(client, [good, bad])
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 1
    assert body["rejections"][0]["index"] == 1


# --------------------------------------------------------------------------- #
# Conversion correlation: billing presence within 5 min before a txn          #
# --------------------------------------------------------------------------- #
def test_conversion_within_window(fresh_db):
    client, db = fresh_db
    # Visitor in billing zone at 18:20:00, transaction at 18:23:00 (within 5 min).
    _ingest(client, [
        _evt("ST1", "ENTRY", "V1", "2026-03-08T18:10:00Z"),
        _evt("ST1", "BILLING_QUEUE_JOIN", "V1", "2026-03-08T18:20:00Z"),
    ])
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO transactions VALUES (?,?,?,?)",
            ("TXN1", "ST1", "2026-03-08T18:23:00Z", 999.0),
        )
    m = client.get("/stores/ST1/metrics").json()
    assert m["converted_visitors"] == 1
    assert m["conversion_rate"] == 1.0


def test_conversion_outside_window(fresh_db):
    client, db = fresh_db
    _ingest(client, [
        _evt("ST1", "ENTRY", "V1", "2026-03-08T18:00:00Z"),
        _evt("ST1", "BILLING_QUEUE_JOIN", "V1", "2026-03-08T18:00:00Z"),
    ])
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO transactions VALUES (?,?,?,?)",
            ("TXN1", "ST1", "2026-03-08T18:30:00Z", 999.0),  # 30 min later
        )
    m = client.get("/stores/ST1/metrics").json()
    assert m["converted_visitors"] == 0


# --------------------------------------------------------------------------- #
# Anomalies                                                                    #
# --------------------------------------------------------------------------- #
def test_queue_spike_anomaly(fresh_db):
    client, _ = fresh_db
    _ingest(client, [
        _evt("ST1", "BILLING_QUEUE_JOIN", "V1", "2026-03-08T18:20:00Z", queue_depth=9),
    ])
    a = client.get("/stores/ST1/anomalies").json()
    types = {x["type"]: x for x in a["anomalies"]}
    assert "QUEUE_SPIKE" in types
    assert types["QUEUE_SPIKE"]["severity"] == "CRITICAL"
    assert types["QUEUE_SPIKE"]["suggested_action"]


def test_dead_zone_anomaly(fresh_db):
    client, _ = fresh_db
    _ingest(client, [
        _evt("ST1", "ZONE_ENTER", "V1", "2026-03-08T18:20:00Z", zone_id="Z_DEAD"),
    ])
    a = client.get("/stores/ST1/anomalies").json()
    assert any(x["type"] == "DEAD_ZONE" for x in a["anomalies"])


# --------------------------------------------------------------------------- #
# Health + reliability                                                         #
# --------------------------------------------------------------------------- #
def test_health_no_data(fresh_db):
    client, _ = fresh_db
    h = client.get("/health").json()
    assert h["status"] == "NO_DATA"


def test_health_stale_feed(fresh_db):
    client, _ = fresh_db
    _ingest(client, [_evt("ST1", "ENTRY", "V1", "2026-03-08T18:10:00Z")])
    h = client.get("/health").json()
    assert h["status"] == "STALE_FEED"  # event is far in the past


def test_health_fresh_feed(fresh_db):
    client, _ = fresh_db
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _ingest(client, [_evt("ST1", "ENTRY", "V1", now)])
    h = client.get("/health").json()
    assert h["status"] == "OK"


def test_db_outage_returns_503(fresh_db, monkeypatch):
    client, _ = fresh_db
    monkeypatch.setenv("STORE_DB_OUTAGE", "1")
    r = client.get("/stores/ST1/metrics")
    assert r.status_code == 503
    assert "stack" not in r.text.lower()


# --------------------------------------------------------------------------- #
# emit.py normaliser                                                           #
# --------------------------------------------------------------------------- #
def test_pos_loader_real_schema(fresh_db, tmp_path):
    """The POS loader must parse the real Purplle schema (DD-MM-YYYY + line items)."""
    _, db = fresh_db
    csv_path = tmp_path / "pos.csv"
    csv_path.write_text(
        "order_id,order_date,order_time,store_id,product_id,brand_name,total_amount\n"
        "1,10-04-2026,12:15:05,ST1008,399945,Faces Canada,302.33\n"
        "2,10-04-2026,12:15:05,ST1008,353621,Faces Canada,491.77\n"  # same basket
        "3,10-04-2026,12:42:18,ST1008,407887,Purplle,100.00\n",
        encoding="utf-8",
    )
    inserted = db.load_pos_csv(str(csv_path))
    assert inserted == 2  # two distinct (store, timestamp) baskets
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT timestamp, basket_value FROM transactions ORDER BY timestamp"
        ).fetchall()
    assert rows[0]["timestamp"] == "2026-04-10T12:15:05Z"
    assert abs(rows[0]["basket_value"] - 794.10) < 0.01  # line items summed


def test_store_id_normalization(fresh_db):
    _, db = fresh_db
    assert db.normalize_store_id("store_1076") == "ST1076"
    assert db.normalize_store_id("PURPLLE_MUM_1076") == "ST1076"
    assert db.normalize_store_id("ST1008") == "ST1008"


def test_emit_normalizer_derives_events():
    import emit
    raw = [
        {"event_type": "entry", "id_token": "ID1", "store_code": "store_1076",
         "camera_id": "cam1", "event_timestamp": "2026-03-08T18:10:00", "is_staff": False},
        {"event_type": "zone_entered", "track_id": 5, "store_id": "ST1076",
         "zone_id": "Z01", "event_time": "2026-03-08T18:11:00"},
        {"event_type": "zone_exited", "track_id": 5, "store_id": "ST1076",
         "zone_id": "Z01", "event_time": "2026-03-08T18:12:00"},  # 60s dwell
        {"event_type": "queue_abandoned", "track_id": 5, "store_id": "ST1076",
         "zone_id": "Z_BILL", "queue_join_ts": "2026-03-08T18:13:00",
         "queue_exit_ts": "2026-03-08T18:14:00", "queue_position_at_join": 4,
         "abandoned": True},
    ]
    events = emit.normalize(raw)
    types = [e["event_type"] for e in events]
    assert "ENTRY" in types
    assert "ZONE_DWELL" in types  # derived because dwell > 30s
    assert "BILLING_QUEUE_JOIN" in types
    assert "BILLING_QUEUE_ABANDON" in types
    assert all(e["store_id"] == "ST1076" for e in events)
