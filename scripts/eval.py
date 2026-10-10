"""
Lane: Data
Score the pipeline against samples/faxes/ground_truth.json. These numbers go
on Devpost and the demo slide.

Reports:
- auto-accept precision: of fields marked green, % that equal ground truth   (target: 100%)
- coverage: % of all fields marked green                                     (honest number, low on bad faxes)
- flag recall: % of expected_flags that the pipeline raised (keyword match on reasons/checks/audit)
- outcome accuracy: order status == expected_outcome
- false "ready_to_fill" count                                               (must be 0)
Each order's run is also sent to PRISM as a trajectory (pipeline.run_fax does that).

Run: python -m scripts.eval [RX-001 RX-003 ...]   (from repo root; ~5 min for all faxes)
Writes samples/eval_results.json.
"""
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from rapidfuzz import fuzz

from backend import config, pipeline, scoring, tracing
from backend.models import FieldName as F, Order, OrderStatus, Status

GREEN = (Status.verified, Status.confirmed)
GT_KEY = {F.npi: "npi_as_written"}
STOP = {"the", "and", "for", "with", "must", "should", "never", "this", "that", "from", "into", "than", "vs",
        "fields", "field", "order", "whole", "route", "confirm", "before", "filling", "after", "one", "all"}


def _gt_value(f: F, gt: dict) -> str | None:
    v = gt.get(GT_KEY.get(f, f.value))
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _order_text(o: Order) -> str:
    bits = [o.status.value, " ".join(o.guardrail_flags), o.callback_draft or ""]
    for ef in o.fields.values():
        bits += ef.reasons + [c.detail for c in ef.checks] + ef.candidates
    bits += [a.detail for a in o.audit]
    if o.pick_ticket:
        bits += o.pick_ticket.warnings
    return " ".join(bits).lower()


def _flag_raised(flag: str, text: str) -> bool:
    toks = [t for t in re.findall(r"[a-z0-9.]+", flag.lower()) if len(t) > 2 and t not in STOP]
    if not toks:
        return fuzz.partial_ratio(flag.lower(), text) >= 70
    hit = sum(1 for t in toks if t in text or fuzz.partial_ratio(t, text) >= 92)
    return hit / len(toks) >= 0.5


def _match_orders(orders: list[Order], gts: list[dict]) -> list[tuple[Order, dict | None]]:
    """Pair pipeline orders with ground-truth orders by drug name (multi-drug faxes)."""
    left = list(gts)
    pairs = []
    for o in orders:
        ef = o.fields.get(F.drug)
        d = ((ef.value or next((r.value for r in ef.readings if r.value), "")) if ef else "").lower()
        best = max(left, key=lambda g: fuzz.ratio(d, str(g.get("drug", "")).lower()), default=None)
        if best is not None:
            left.remove(best)
        pairs.append((o, best))
    return pairs


def evaluate(ids: list[str] | None = None) -> dict:
    gt = json.loads((config.SAMPLES_DIR / "ground_truth.json").read_text())
    cases = {c["id"]: c for c in (gt if isinstance(gt, list) else gt.values())}
    ids = ids or sorted(cases)
    green_total = green_right = fields_total = 0
    flags_total = flags_hit = 0
    outcomes_right = false_ready = 0
    rows = []

    def run(i):
        t = time.time()
        return i, pipeline.run_fax(config.SAMPLES_DIR / cases[i]["file"]), time.time() - t

    with ThreadPoolExecutor(3) as ex:
        results = list(ex.map(run, ids))

    for i, orders, secs in results:
        c = cases[i]
        gts = c.get("orders") or ([c["ground_truth"]] if c.get("ground_truth") else [])
        exp = c["expected_outcome"]
        statuses = [o.status.value for o in orders]
        outcome_ok = all(s == exp for s in statuses) and len(orders) == max(len(gts), 1)
        outcomes_right += outcome_ok
        fr = [o.id for o in orders if o.status == OrderStatus.ready_to_fill and exp != "ready_to_fill"]
        false_ready += len(fr)
        text = " ".join(_order_text(o) for o in orders)
        raised = {fl: _flag_raised(fl, text) for fl in c.get("expected_flags", [])}
        flags_total += len(raised)
        flags_hit += sum(raised.values())
        g_tot = g_ok = f_tot = 0
        wrong_green = []
        for o, g in _match_orders(orders, gts):
            if g is None:
                continue
            for f in F:
                want = _gt_value(f, g)
                if want is None or f not in o.fields:
                    continue
                f_tot += 1
                ef = o.fields[f]
                if ef.status in GREEN:
                    g_tot += 1
                    ok = scoring.normalize(f, ef.value) == scoring.normalize(f, want)
                    g_ok += ok
                    if not ok:
                        wrong_green.append(f"{f.value}: got {ef.value!r}, truth {want!r}")
        green_total += g_tot
        green_right += g_ok
        fields_total += f_tot
        rows.append({"id": i, "kind": c.get("kind", "pad"), "expected": exp, "got": statuses, "outcome_ok": outcome_ok,
                     "green": g_tot, "green_correct": g_ok, "fields": f_tot, "wrong_green": wrong_green,
                     "flags": raised, "false_ready": fr, "seconds": round(secs, 1)})

    summary = {
        "faxes": len(ids),
        "auto_accept_precision": round(green_right / green_total, 4) if green_total else None,
        "coverage": round(green_total / fields_total, 4) if fields_total else None,
        "flag_recall": round(flags_hit / flags_total, 4) if flags_total else None,
        "outcome_accuracy": round(outcomes_right / len(ids), 4),
        "false_ready_to_fill": false_ready,
        "green_fields": green_total, "green_correct": green_right, "fields": fields_total,
        "flags_expected": flags_total, "flags_raised": flags_hit,
    }
    return {"summary": summary, "faxes": rows}


def main() -> int:
    res = evaluate([a for a in sys.argv[1:] if a.startswith("RX")] or None)
    print(f"{'fax':7} {'kind':18} {'expected':20} {'got':38} {'green':9} {'flags':6} {'s':>4}")
    for r in res["faxes"]:
        got = ",".join(r["got"])
        flags = f"{sum(r['flags'].values())}/{len(r['flags'])}" if r["flags"] else "-"
        mark = "OK " if r["outcome_ok"] else "BAD"
        print(f"{r['id']:7} {r['kind']:18} {r['expected']:20} {mark} {got[:34]:34} "
              f"{r['green_correct']:>2}/{r['green']:<2}/{r['fields']:<2} {flags:6} {r['seconds']:4.0f}")
        for w in r["wrong_green"]:
            print(f"        WRONG GREEN  {w}")
        for fl, hit in r["flags"].items():
            if not hit:
                print(f"        missed flag: {fl[:100]}")
    s = res["summary"]
    print("\nauto-accept precision (green fields that equal ground truth):", s["auto_accept_precision"],
          f"({s['green_correct']}/{s['green_fields']})")
    print("coverage (fields auto-accepted):                            ", s["coverage"],
          f"({s['green_fields']}/{s['fields']})")
    print("flag recall (expected flags raised):                        ", s["flag_recall"],
          f"({s['flags_raised']}/{s['flags_expected']})")
    print("outcome accuracy (status == expected):                      ", s["outcome_accuracy"])
    print("false ready_to_fill (must be 0):                            ", s["false_ready_to_fill"])
    (config.SAMPLES_DIR / "eval_results.json").write_text(json.dumps(res, indent=2, default=str))
    print("\nwrote samples/eval_results.json")
    tracing.flush()
    return 1 if s["false_ready_to_fill"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
