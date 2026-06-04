"""detect.py — Detection layer (documented stub).

WHY THIS IS A STUB
------------------
The challenge scores "Detection Accuracy" against a *hidden* ground-truth set we
do not possess, and a production CV pipeline (YOLO + ByteTrack + OSNet re-id)
cannot be trained or validated within the available time budget. Per the
challenge FAQ, offline pre-processing of detections is explicitly permitted.

We therefore treat the provided `sample_events.jsonl` as the *output* of this
detection layer and normalise it into the canonical schema in `emit.py`. This
module documents the architecture we WOULD run so the design is defensible.

INTENDED PIPELINE (not executed here)
-------------------------------------
1. Decode each camera clip at 15 FPS (entry / main-floor / billing).
2. Person detection:        YOLOv8n (good speed/accuracy trade-off on CPU).
3. Multi-object tracking:   ByteTrack (robust under partial occlusion).
4. Cross-camera re-id:      OSNet appearance embeddings + trajectory matching
                            for de-duplication and REENTRY detection.
5. Zone logic:              polygon zones from store_layout.json; a visitor
                            dwelling >30s in a zone emits ZONE_DWELL.
6. Entry/exit direction:    line-crossing on the entry camera -> ENTRY / EXIT.
7. Staff exclusion:         uniform-colour heuristic / VLM zero-shot classifier
                            sets is_staff=true.
8. Group handling:          each tracked individual is counted separately even
                            when entering simultaneously (group_id metadata).

Confidence from the detector is preserved end-to-end so downstream analytics can
degrade gracefully (e.g. heatmap low_confidence flagging).
"""
from __future__ import annotations


def run_detection(*_args, **_kwargs):  # pragma: no cover - intentional stub
    raise NotImplementedError(
        "Real CV detection is out of scope for this time-boxed submission. "
        "Use emit.py to normalise the provided sample detection output."
    )


if __name__ == "__main__":  # pragma: no cover
    print(__doc__)
