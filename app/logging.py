"""Structured JSON request logging middleware."""
from __future__ import annotations

import json
import sys
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
        start = time.perf_counter()

        # Best-effort store_id extraction from the path.
        store_id = None
        parts = request.url.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "stores":
            store_id = parts[1]

        request.state.trace_id = trace_id
        request.state.event_count = 0

        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["x-trace-id"] = trace_id
            return response
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            log = {
                "trace_id": trace_id,
                "store_id": store_id,
                "endpoint": request.url.path,
                "method": request.method,
                "latency_ms": latency_ms,
                "event_count": getattr(request.state, "event_count", 0),
                "status_code": status_code,
            }
            sys.stdout.write(json.dumps(log) + "\n")
            sys.stdout.flush()
