"""
PRISM tracing. Every pipeline step calls `trace(...)` with its own agent_name
so runs show up in PRISM's flow views (runs without agent_name land under
"unknown"). session_id = order id, so one fax = one PRISM session.

Uses the documented HTTP endpoint (POST /api/traces, X-PRISMtrace-Key header).
If keys are missing it no-ops, so local dev never breaks.

Note from the PRISM docs: on SDK/HTTP paths Guardrails FLAG, they don't block.
So our code reads the flags back and enforces the block itself (see guard.py).
"""
import json
import time
import uuid
from contextlib import contextmanager

import httpx

from backend import config

_client = httpx.Client(timeout=5.0)


def enabled() -> bool:
    return bool(config.PRISMTRACE_API_KEY and config.PRISMTRACE_PROJECT_ID)


def trace(step: str, session_id: str, model: str, input_obj, output_obj, latency_ms: int,
          metadata: dict | None = None) -> str | None:
    """Send one trace. Returns a local trace id for the audit trail."""
    trace_id = str(uuid.uuid4())
    if not enabled():
        return trace_id
    body = {
        "project_id": config.PRISMTRACE_PROJECT_ID,
        "agent_name": f"{config.AGENT_PREFIX}-{step}",
        "session_id": session_id,
        "model": model or "none",
        "input_messages": [{"role": "user", "content": _s(input_obj)}],
        "output_message": {"role": "assistant", "content": _s(output_obj)},
        "latency_ms": latency_ms,
        "metadata": {"trace_id": trace_id, **(metadata or {})},
    }
    try:
        _client.post(f"{config.PRISMTRACE_HOST}/api/traces", json=body,
                     headers={"X-PRISMtrace-Key": config.PRISMTRACE_API_KEY})
    except httpx.HTTPError:
        pass  # tracing must never take the pipeline down
    return trace_id


@contextmanager
def step(name: str, session_id: str, model: str = "", input_obj=None):
    """with tracing.step("verify", order.id, input_obj=fields) as t: t["output"] = result"""
    box = {"output": None, "metadata": {}, "trace_id": None}
    t0 = time.perf_counter()
    try:
        yield box
    finally:
        box["trace_id"] = trace(name, session_id, model, input_obj, box["output"],
                                int((time.perf_counter() - t0) * 1000), box["metadata"])


def handshake() -> dict:
    """Check credentials via PRISM setup doctor. Run: python -m backend.tracing"""
    r = _client.post(f"{config.PRISMTRACE_HOST}/api/setup-doctor/handshake",
                     headers={"X-PRISMtrace-Key": config.PRISMTRACE_API_KEY},
                     json={"project_id": config.PRISMTRACE_PROJECT_ID})
    return {"status": r.status_code, "body": r.text[:500]}


def _s(obj) -> str:
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, default=str)[:20000]
    except TypeError:
        return str(obj)[:20000]


if __name__ == "__main__":
    print(handshake() if enabled() else "Set PRISMTRACE_API_KEY and PRISMTRACE_PROJECT_ID in .env first")
