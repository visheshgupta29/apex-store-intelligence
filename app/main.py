"""Store Intelligence API — FastAPI application entrypoint.

Run: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .anomalies import detect_anomalies
from .db import DatabaseOutage, get_conn, init_db, load_pos_csv
from .funnel import compute_funnel
from .heatmap import compute_heatmap
from .ingestion import MAX_BATCH, ingest_events
from .logging import StructuredLoggingMiddleware
from .metrics import compute_metrics, parse_ts
from .models import IngestRequest

STALE_FEED_SECONDS = 600

app = FastAPI(title="Apex Retail — Store Intelligence API", version="1.0.0")
app.add_middleware(StructuredLoggingMiddleware)


@app.exception_handler(DatabaseOutage)
async def _db_outage_handler(request: Request, exc: DatabaseOutage) -> JSONResponse:
    # Graceful degradation: never leak stack traces; signal unavailability.
    return JSONResponse(
        status_code=503,
        content={"error": "database_unavailable", "detail": "Service temporarily unavailable."},
    )


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "detail": "An unexpected error occurred."},
    )


@app.on_event("startup")
def _startup() -> None:
    init_db()
    pos_csv = os.environ.get("POS_CSV")
    if pos_csv:
        loaded = load_pos_csv(pos_csv)
        print(f'{{"event": "pos_loaded", "transactions": {loaded}}}')


@app.post("/events/ingest")
def ingest(payload: IngestRequest, request: Request):
    if len(payload.events) > MAX_BATCH:
        return JSONResponse(
            status_code=413,
            content={"error": "batch_too_large", "max": MAX_BATCH, "received": len(payload.events)},
        )
    result = ingest_events(payload.events)
    request.state.event_count = result.received
    # 200 even on partial success; body carries per-event outcome.
    return result.model_dump()


@app.get("/stores/{store_id}/metrics")
def store_metrics(store_id: str):
    with get_conn() as conn:
        return compute_metrics(conn, store_id)


@app.get("/stores/{store_id}/funnel")
def store_funnel(store_id: str):
    with get_conn() as conn:
        return compute_funnel(conn, store_id)


@app.get("/stores/{store_id}/heatmap")
def store_heatmap(store_id: str):
    with get_conn() as conn:
        return compute_heatmap(conn, store_id)


@app.get("/stores/{store_id}/anomalies")
def store_anomalies(store_id: str):
    with get_conn() as conn:
        return detect_anomalies(conn, store_id)


@app.get("/health")
def health():
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(timestamp) AS last_ts FROM events").fetchone()
    last_ts = row["last_ts"] if row else None
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    feed_lag_s = None
    status = "OK"
    if last_ts:
        parsed = parse_ts(last_ts)
        if parsed:
            feed_lag_s = max(0, int((now - parsed).total_seconds()))
            if feed_lag_s > STALE_FEED_SECONDS:
                status = "STALE_FEED"
    else:
        status = "NO_DATA"

    return {
        "status": status,
        "service": "store-intelligence",
        "last_event_timestamp": last_ts,
        "feed_lag_seconds": feed_lag_s,
        "stale_threshold_seconds": STALE_FEED_SECONDS,
    }


# Live dashboard (web UI). Mounted last so API routes take precedence.
_dashboard_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard")
if os.path.isdir(_dashboard_dir):
    app.mount("/dashboard", StaticFiles(directory=_dashboard_dir, html=True), name="dashboard")
