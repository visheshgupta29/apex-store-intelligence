# Apex Retail — Store Intelligence System

Transforms raw CCTV-derived detection events into real-time retail analytics:
unique visitors, **offline conversion rate** (the North Star), dwell heatmaps,
a conversion funnel, anomaly detection, and a live web dashboard.

> **North Star — Offline Store Conversion Rate** = visitors who purchased ÷ total unique visitors.

## Architecture

```
Raw CCTV ──▶ Detection Layer ──▶ emit.py ──▶ POST /events/ingest ──▶ SQLite ──▶ Intelligence API ──▶ Dashboard
(videos)     (detect.py stub)   (normalise)   (validate/dedupe)               (metrics/funnel/...)   (web UI)
```

- **Detection layer** (`detect.py`): documents the intended YOLOv8 + ByteTrack + OSNet pipeline. Per the challenge FAQ, offline pre-processing is permitted, so the provided `sample_events.jsonl` is treated as detection output.
- **`emit.py`**: normalises the heterogeneous raw feed into the **canonical event schema** and *derives* `ZONE_DWELL`, `REENTRY`, `BILLING_QUEUE_JOIN/ABANDON`. Replays events to the API.
- **Intelligence API** (`app/`): FastAPI + SQLite. Ingestion, metrics, funnel, heatmap, anomalies, health.
- **Dashboard** (`dashboard/index.html`): polls `/metrics`, `/funnel`, `/anomalies`, `/health` every 3s.

## Quick start (Docker — acceptance gate)

```bash
cd store-intelligence
docker compose up --build
# API:        http://localhost:8000
# Dashboard:  http://localhost:8000/dashboard
# Docs:       http://localhost:8000/docs
```

## Quick start (local)

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000
# In another shell — replay the sample detection feed:
python emit.py --api http://localhost:8000 --file ../data/sample_eventsbe42122.jsonl
```

Optional POS correlation (enables conversion): set `POS_CSV` to a transactions CSV
before startup (supports both the canonical and the real Purplle line-item schema).

## API

| Method | Endpoint | Description |
|---|---|---|
| POST | `/events/ingest` | Ingest ≤500 events. Validated, deduplicated, idempotent, partial success. |
| GET | `/stores/{id}/metrics` | Unique visitors, conversion rate, avg dwell, queue depth, abandonment. |
| GET | `/stores/{id}/funnel` | Entry → Zone → Billing → Purchase with drop-off %. |
| GET | `/stores/{id}/heatmap` | Zone popularity + dwell, scored 0–100, low-confidence flag <20 sessions. |
| GET | `/stores/{id}/anomalies` | Queue spike, conversion drop, dead zones (INFO/WARN/CRITICAL + action). |
| GET | `/health` | Status, last event timestamp, feed lag; `STALE_FEED` after 10 min. |

## Testing

```bash
pytest --cov=app --cov=emit
```

18 tests, **86% coverage** (gate ≥70%). Covers the four required scenarios
(empty store, all-staff, zero purchases, re-entry) plus idempotency, partial
success, the 5-minute conversion window, anomalies, and the 503 DB-outage path.

## Edge-case handling

- **Staff exclusion** — `is_staff` filtered out of every customer metric.
- **Re-entry** — emitted as `REENTRY`; funnel/metrics dedupe on `visitor_id`.
- **Groups** — each individual counted separately despite shared `group_id`.
- **Empty store** — endpoints return zeros, never errors.
- **Cross-feed store-id mismatch** — normalised (`store_1076`/`PURPLLE_MUM_1076` → `ST1076`).
- **DB outage** — returns HTTP 503, no stack traces.

See `DESIGN.md` and `CHOICES.md` for architecture and decision records.
