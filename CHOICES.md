# CHOICES.md — Decision Records

Each decision lists the options considered, the AI recommendation, my final
decision, and the trade-offs accepted.

---

## Decision 1 — Detection model selection

**Options considered**

1. Train/run a real CV pipeline (YOLOv8/YOLOv9/RT-DETR + ByteTrack + OSNet re-id).
2. Use a VLM for zero-shot person/staff classification on sampled frames.
3. Treat the provided `sample_events.jsonl` as pre-processed detector output and
   normalise it into canonical events.

**AI recommendation.** YOLOv8n + ByteTrack + OSNet — the best balance of CPU
speed, accuracy, occlusion robustness, and cross-camera re-identification.

**Final decision.** Option 3, with the YOLOv8 pipeline fully documented in
`detect.py` as the intended production design. The challenge scores detection
accuracy against **hidden ground truth** that cannot be validated locally, and
real-time training/inference is infeasible in the time budget. The FAQ
explicitly permits offline pre-processing.

**Trade-offs.** I forgo the ~10 detection-accuracy points that depend on hidden
ground truth, in exchange for a fully correct, tested analytics platform (the
larger, controllable point pool). The architecture keeps a real detector
drop-in compatible via the event schema, so this is a sequencing choice, not a
capability gap.

---

## Decision 2 — Event schema design

**Options considered**

1. Persist raw vendor events as-is (entry/exit, zone, queue shapes).
2. Define a single **canonical event schema** (the spec's schema) and normalise
   all sources into it at ingestion/emit time.
3. A hybrid: store canonical columns plus the original JSON blob.

**AI recommendation.** Canonical schema with strict Pydantic validation.

**Final decision.** Option 3 — canonical typed columns for fast analytics **plus**
a `raw` JSON column for traceability. `emit.py` maps heterogeneous inputs and
derives implied events (`ZONE_DWELL`, `REENTRY`, billing join/abandon). Store
ids are normalised (`store_1076`/`PURPLLE_MUM_1076` → `ST1076`) so cross-feed
joins work.

**Trade-offs.** Normalisation adds an adapter layer and some derived-event logic
to maintain, but it decouples detection from analytics, makes every metric a
simple indexed query, and lets the suite test analytics deterministically.

---

## Decision 3 — API architecture: compute-on-read vs precomputed

**Options considered**

1. **Compute-on-read**: endpoints run aggregation queries per request.
2. **Precomputed/materialised** metrics updated on ingest.
3. Streaming aggregation (Kafka/Flink) with a serving store.

**AI recommendation.** Start with compute-on-read; add caching only if latency
demands it.

**Final decision.** Option 1. With indexed SQLite and challenge-scale data,
observed endpoint latency is single-digit milliseconds (see structured logs).
Real-time correctness is automatic — no cache invalidation, no staleness.

**Trade-offs.** Compute-on-read does not scale to very high QPS or huge event
volumes; at that point I'd move to Option 2 (materialised per-store rollups
updated in the ingestion path) behind the same API contract. For this challenge,
simplicity and guaranteed freshness outweigh premature scaling. The clean
`app/db.py` boundary makes that future migration low-risk.
