# DESIGN.md — Store Intelligence System

## 1. Problem framing and guiding principle

The challenge converts raw CCTV footage into retail analytics whose North Star
is the **offline store conversion rate**. Every component is judged by whether it
improves the accuracy or usefulness of that metric. With a severe time budget, I
optimised for a fully working, correct, observable **Intelligence API** and a
defensible, well-documented detection strategy rather than an unverifiable
computer-vision model scored against hidden ground truth.

## 2. System architecture

```
Raw CCTV → Detection Layer (detect.py) → emit.py (normalise/derive)
        → POST /events/ingest → SQLite → Intelligence API → Web Dashboard
```

The detection layer is decoupled from analytics by a **canonical event schema**.
This is the key design decision: the API consumes events, not pixels, so the
detector can be a real CV pipeline, a replay of pre-processed detections, or a
simulator — all interchangeable. `emit.py` is the adapter that normalises the
provided heterogeneous feed (entry/exit, zone, queue records with inconsistent
field names and store ids) into canonical events, and **derives** events the raw
feed only implies: `ZONE_DWELL` (dwell > 30 s), `REENTRY` (a visitor entering
again after an exit), and `BILLING_QUEUE_JOIN/ABANDON`.

## 3. Data model

Two SQLite tables: `events` (keyed by `event_id` for idempotency and dedup) and
`transactions`. Indexes on `(store_id, event_type)` and `(store_id, timestamp)`
serve every aggregation. SQLite was chosen for zero-config, file-backed
durability that runs cleanly under `docker compose up`; the access layer is
isolated in `app/db.py` so a Postgres swap touches one module.

## 4. Conversion correlation (North Star)

No customer identifiers exist, so conversion is inferred: a visitor present in
the billing zone within **5 minutes before** a POS transaction is `converted`.
Each transaction greedily claims at most one unmatched billing visitor, avoiding
double-counting. Store ids are normalised across feeds before joining.

## 5. Reliability and observability

Structured JSON logs (`trace_id`, `store_id`, `endpoint`, `latency_ms`,
`event_count`, `status_code`) wrap every request. Ingestion is idempotent
(`INSERT OR IGNORE`) with partial success. A simulated DB outage returns HTTP
503; unhandled errors return clean JSON with no stack traces. `/health` reports
`STALE_FEED` when no event arrives within 10 minutes.

## 6. AI-Assisted Decisions

- **Detection model trade-off.** AI suggested YOLOv8n + ByteTrack + OSNet for the
  speed/accuracy/occlusion/re-id balance. **I agreed** on the *design* but
  **declined to run it**: the detection-accuracy points are scored against hidden
  ground truth I cannot validate against, making real CV a poor time investment.
  I documented the pipeline in `detect.py` and invested in the API instead.
- **Schema-first decoupling.** AI recommended an event-stream boundary between
  detection and analytics. **I agreed** — it let me treat `sample_events.jsonl`
  as detector output (explicitly permitted) and build/test analytics
  deterministically.
- **Database choice.** AI proposed SQLite vs Postgres/DuckDB. **I agreed** with
  SQLite for this scope; trade-off is single-writer concurrency, acceptable here.
- **Conversion greedy-match.** AI proposed the 5-minute window join; **I refined**
  it to claim each transaction to a single unmatched visitor so re-billing visits
  don't inflate conversion. I verified this with dedicated unit tests
  (`test_conversion_within_window`, `test_conversion_outside_window`).

Where I disagreed with AI, it was about *effort allocation under a deadline*, not
correctness — a deliberate engineering trade-off favouring verifiable, scored
output over an impressive but unverifiable model.
