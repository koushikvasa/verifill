"""
Lane: Judge-proofing (H8)
Run every file in samples/robustness/ through the upload path, in order, and compare to
expected.json. RX-001 is run first so J04 (a resend of it) can be caught as a duplicate.
Exit code 1 if anything crashed or anything not expected to be ready_to_fill came out ready.

Run: python -m scripts.robustness
"""
import json
import sys
import time
import traceback

from backend import config, pipeline, tracing
from backend.models import OrderStatus, Status

KIT = config.ROOT / "samples" / "robustness"


def top_reason(o) -> str:
    if o.guardrail_flags:
        return o.guardrail_flags[0]
    for ef in o.fields.values():
        if ef.status not in (Status.verified, Status.confirmed) and ef.reasons:
            return f"{ef.name.value}: {ef.reasons[0]}"
    for a in reversed(o.audit):
        if a.actor in ("classify", "refill", "ingest", "duplicate", "pipeline") and a.action not in ("received", "done", "page"):
            return f"{a.actor}: {a.detail}"
    return o.pick_ticket.warnings[0] if o.pick_ticket and o.pick_ticket.warnings else ""


def main() -> int:
    expected = json.loads((KIT / "expected.json").read_text())
    pipeline.clear()
    pipeline.run_fax(config.SAMPLES_DIR / "RX-001_fax.png", dedupe=True)   # so J04 is a resend
    bad = crashed = 0
    print(f"{'file':34} {'expected':20} {'got':34} {'s':>4}  top reason")
    for e in expected:
        t = time.time()
        try:
            orders = pipeline.run_fax(KIT / e["file"], dedupe=True)
            got = [o.status.value for o in orders]
            reason = top_reason(orders[0])
        except Exception as ex:  # noqa: BLE001
            crashed += 1
            got, reason = ["CRASH"], f"{type(ex).__name__}: {ex}"
            traceback.print_exc(limit=3)
            orders = []
        ok = got and all(g == e["expected_outcome"] for g in got)
        bad += not ok
        if e["expected_outcome"] != "ready_to_fill" and any(o.status == OrderStatus.ready_to_fill for o in orders):
            print("   !!! FALSE READY_TO_FILL")
            crashed += 1
        print(f"{e['file']:34} {e['expected_outcome']:20} {'OK ' if ok else 'BAD'} {','.join(got)[:30]:30} "
              f"{time.time() - t:4.0f}  {reason[:110]}")
    print(f"\n{len(expected) - bad}/{len(expected)} as expected; crashes/false-ready: {crashed}")
    tracing.flush()
    return 1 if crashed else 0


if __name__ == "__main__":
    raise SystemExit(main())
