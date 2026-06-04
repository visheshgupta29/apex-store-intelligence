"""Seed a running Store Intelligence instance over HTTP (demo helper).

Rebases bundled seed timestamps to "now" so /health reads OK, then POSTs them.
Usage: python3 scripts/seed_remote.py <base_url>
Skips TLS verification to tolerate corporate MITM proxies (demo only).
"""
import datetime as dt
import json
import os
import ssl
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(HERE, "seed", "seed_events.json")

events = json.load(open(SEED, encoding="utf-8"))
parsed = [dt.datetime.fromisoformat(e["timestamp"].replace("Z", "")) for e in events]
offset = dt.datetime.utcnow() - max(parsed)
for e, p in zip(events, parsed):
    e["timestamp"] = (p + offset).strftime("%Y-%m-%dT%H:%M:%SZ")

payload = json.dumps({"events": events}).encode()
req = urllib.request.Request(
    f"{BASE}/events/ingest",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
    print("ingest:", resp.status, resp.read().decode())
