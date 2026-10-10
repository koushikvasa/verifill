"""
PRISM by Block Convey: tracing, Knowledge Base, trajectory evaluation.

Uses the official SDK (prismtrace-sdk>=0.5.1, `PRISMtrace` client):
- every pipeline stage -> pt.trace_llm(..., agent_name="verifill-<stage>", session_id=order.id)
- end of each order    -> pt.submit_trajectory(steps, agent_name="verifill-pipeline") for PRISM evaluation
- grounding            -> pt.kb_search(query) against the project Knowledge Base (rule 4)

Never crashes the pipeline: if keys are missing or PRISM is unreachable, calls
no-op and the audit trail still gets a local trace id.

Note: on SDK/HTTP paths PRISM Guardrails FLAG, they don't block, so guard.py
enforces the block itself.
"""
import json
import time
import uuid
from contextlib import contextmanager

from backend import config

_pt = None
_steps: dict[str, list[dict]] = {}   # session_id -> trajectory steps


def enabled() -> bool:
    return bool(config.PRISMTRACE_API_KEY and config.PRISMTRACE_PROJECT_ID)


def client():
    """Lazy singleton PRISMtrace client (None if disabled or SDK missing)."""
    global _pt
    if _pt is None and enabled():
        try:
            from prismtrace import PRISMtrace
            _pt = PRISMtrace(api_key=config.PRISMTRACE_API_KEY, host=config.PRISMTRACE_HOST,
                             project_id=config.PRISMTRACE_PROJECT_ID, timeout=10)
        except Exception:
            _pt = None
    return _pt


def trace(step: str, session_id: str, model: str, input_obj, output_obj, latency_ms: int,
          metadata: dict | None = None) -> str:
    """Record one stage. Returns a trace id for the audit trail."""
    trace_id = str(uuid.uuid4())
    _steps.setdefault(session_id, []).append(
        {"step_type": "tool_call", "label": step, "tool_name": f"{config.AGENT_PREFIX}-{step}",
         "input_summary": _s(input_obj)[:4000], "output_summary": _s(output_obj)[:4000],
         "duration_ms": latency_ms, "status": "success"})
    pt = client()
    if pt is None:
        return trace_id
    try:
        pt.trace_llm(model=model or "none",
                     input_messages=[{"role": "user", "content": _s(input_obj)}],
                     output=_s(output_obj), latency_ms=latency_ms, trace_id=trace_id,
                     agent_name=f"{config.AGENT_PREFIX}-{step}", session_id=session_id,
                     metadata={"stage": step, **(metadata or {})})
    except Exception:
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


def submit_order(session_id: str, final_status: str, summary: str = "",
                 async_send: bool = True) -> dict | None:
    """Send the whole order run as one trajectory so PRISM's Evaluator scores it.
    Call once at the end of pipeline.run(). final_status: "success" | "failure".
    summary: the order outcome (e.g. status + flagged fields), recorded as the final step."""
    steps = _steps.pop(session_id, [])
    pt = client()
    if pt is None or not steps:
        return None
    steps.append({"step_type": "final_answer", "label": "order outcome",
                  "output_summary": summary or final_status, "status": "success"})
    try:
        return pt.submit_trajectory(steps, agent_name=f"{config.AGENT_PREFIX}-pipeline",
                                    conversation_id=session_id, final_status=final_status,
                                    async_send=async_send)
    except Exception:
        return None


def kb_search(query: str, limit: int = 5) -> list[dict]:
    """Ground a claim in the PRISM Knowledge Base. Empty list if unavailable;
    callers fall back to backend/data and mark grounded_in="local"."""
    pt = client()
    if pt is None:
        return []
    try:
        return pt.kb_search(query, limit=limit) or []
    except Exception:
        return []


def flush():
    pt = client()
    if pt is not None:
        try:
            pt.flush()
        except Exception:
            pass


def _s(obj) -> str:
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, default=str)[:20000]
    except TypeError:
        return str(obj)[:20000]


if __name__ == "__main__":
    # Live smoke test: one real trace + KB listing. Then run: python -m prismtrace.verify
    if not enabled():
        raise SystemExit("Set PRISMTRACE_API_KEY and PRISMTRACE_PROJECT_ID in .env first")
    sid = f"smoke-{uuid.uuid4().hex[:6]}"
    print("trace id:", trace("smoke", sid, "none", "verifill smoke test", {"ok": True}, 1))
    print("trajectory:", submit_order(sid, "success", "smoke ok", async_send=False))
    flush()
    try:
        print("kb documents:", [d.get("name") for d in client().kb_list_documents()])
    except Exception as e:
        print("kb list failed:", e)
