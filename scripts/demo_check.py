"""Demo dry run against the live server (uvicorn on :8000), in the PLAN.md order + judge moments.
Run right before the demo: python -m scripts.demo_check   (~3 min, prints ok/FAIL per step)."""
import time

import httpx

B = "http://localhost:8000"
c = httpx.Client(timeout=240)
P = "J. Patel, PharmD"
fails = []


def step(title):
    print(f"\n=== {title}")


def check(cond, what):
    print(("   ok   " if cond else "   FAIL ") + what)
    if not cond:
        fails.append(what)


def fx(o, f):
    return o["fields"][f]


def run(fax):
    t = time.time()
    r = c.post(f"{B}/faxes/sample/{fax}")
    check(r.status_code == 200, f"{fax} HTTP {r.status_code} in {time.time() - t:.0f}s")
    return r.json()


step("0. health + clear")
h = c.get(f"{B}/health").json()
check(h["prism"] and h["nicrron"], f"health {h}")
check(c.post(f"{B}/orders/clear").status_code == 200, "clear all")

step("1. RX-001 clean -> all green -> Aisle 3 Shelf B Bin 12 -> ready to fill")
o = run("RX-001")[0]
check(o["status"] == "ready_to_fill", f"status {o['status']}")
check(all(v["status"] == "verified" for v in o["fields"].values()), "all 13 fields green")
pt = o["pick_ticket"]
check(pt["status"] == "in_stock" and (pt["item"]["aisle"], pt["item"]["shelf"], pt["item"]["bin"]) == ("3", "B", "12"),
      f"pick ticket {pt['status']} at {pt['item'] and (pt['item']['aisle'], pt['item']['shelf'], pt['item']['bin'])}")
check(all(v["checks"] for k, v in o["fields"].items() if k in ("drug", "strength", "npi")), "drug/strength/npi have grounded checks")
check(any(ch["grounded_in"] == "prism_kb" for v in o["fields"].values() for ch in v["checks"]), "at least one check grounded in PRISM KB")
check(all(r["bbox"] for r in fx(o, "drug")["readings"] if r["source"] == "ocr"), "OCR reading carries a bbox for the UI")
check(c.get(f"{B}/orders/{o['id']}/page.png").status_code == 200, "page.png served")
s = c.post(f"{B}/orders/{o['id']}/scan", json={"ndc": "99101-0110-30"}).json()
check(s["match"], f"scan right bottle: {s['detail'][:60]}")
s = c.post(f"{B}/orders/{o['id']}/scan", json={"ndc": "99101-0120-30"}).json()
check(not s["match"] and "WRONG" in s["detail"], f"scan wrong bottle: {s['detail'][:60]}")
check(all(a.get("at") for a in o["audit"]) and any(a["actor"] == "safety_gate" for a in o["audit"]), f"audit trail {len(o['audit'])} events incl. safety gate")

step("2. RX-003 smudged Hy??zine -> red, both candidates, 'itching' as evidence, inventory locked -> pharmacist picks")
o = run("RX-003")[0]
d = fx(o, "drug")
check(o["status"] == "needs_pharmacist", f"status {o['status']}")
check(d["status"] == "conflict", f"drug is red ({d['status']})")
check(set(d["candidates"]) == {"hydroxyzine", "hydralazine"}, f"candidates {d['candidates']}")
check(any("itching" in r and r.startswith("evidence only") for r in d["reasons"]), "sig 'itching' shown as evidence only")
check(o["pick_ticket"]["status"] == "blocked", f"pick ticket {o['pick_ticket']['status']}: {o['pick_ticket']['warnings'][0][:50]}")
r = c.post(f"{B}/orders/{o['id']}/resolve", json={"field": "drug", "value": "hydroxyzine"})
check(r.status_code == 422, f"resolve without pharmacist name refused ({r.status_code})")
o2 = c.post(f"{B}/orders/{o['id']}/resolve", json={"field": "drug", "value": "hydroxyzine", "pharmacist": P}).json()
check(fx(o2, "drug")["status"] == "confirmed", "drug confirmed by pharmacist")
check(o2["pick_ticket"]["status"] == "in_stock" and o2["pick_ticket"]["item"]["drug"] == "hydroxyzine",
      f"pick ticket unlocked: {o2['pick_ticket']['item'] and o2['pick_ticket']['item']['drug']} at bin {o2['pick_ticket']['item'] and o2['pick_ticket']['item']['bin']}")
check(any(a["actor"].startswith("pharmacist") for a in o2["audit"]), "pharmacist action in audit")
for f, v in o2["fields"].items():
    if v["status"] in ("review", "conflict"):
        o2 = c.post(f"{B}/orders/{o['id']}/resolve", json={"field": f, "value": v["value"] or fx(o2, f)["candidates"][0] if (v["value"] or fx(o2, f)["candidates"]) else "x", "pharmacist": P}).json()
check(o2["status"] == "ready_to_fill", f"after confirming the rest: {o2['status']}")

step("3. RX-004 levothyroxine 0.5 mg -> transcribed as written AND caught as 10x (KB) -> callback draft")
o = run("RX-004")[0]
st = fx(o, "strength")
check(st["value"] == "0.5 mg" or any(r["value"] == "0.5 mg" for r in st["readings"]), f"strength transcribed as written: {st['value']} (readings {[r['value'] for r in st['readings']]})")
check(st["status"] == "conflict" and any("0.05" in ch["detail"] for ch in st["checks"] if not ch["passed"]),
      f"strength red with decimal/marketed check: {[ch['detail'][:50] for ch in st['checks'] if not ch['passed']][:1]}")
check(any(ch["grounded_in"] == "prism_kb" for ch in st["checks"]), "strength check grounded in PRISM KB")
check(o["callback_draft"] and "0.5 mg" in o["callback_draft"] and "phone" in o["callback_draft"], "callback draft names the issue + registry phone")
r = c.post(f"{B}/orders/{o['id']}/resolve", json={"field": "strength", "value": "0.5 mg", "pharmacist": P})
check(r.status_code in (200, 409), f"confirming 0.5 mg without a reason -> {r.status_code} (409 expected once drug is known)")
print("      callback:", o["callback_draft"].splitlines()[0][:90])

step("4. RX-009 injection fax -> guardrail blocks it live")
o = run("RX-009")[0]
check(o["status"] == "rejected" and o["guardrail_flags"], f"{o['status']}: {o['guardrail_flags'][0][:70]}")
check(not o["fields"], "no fields extracted (blocked before any model call)")
r = c.post(f"{B}/orders/{o['id']}/resolve", json={"field": "drug", "value": "x", "pharmacist": P})
check(r.status_code == 409, f"rejected order cannot be resolved field by field ({r.status_code})")

step("5. RX-008 worst case -> refuses to auto-fill")
o = run("RX-008")[0]
check(o["status"] == "needs_pharmacist" and o["pick_ticket"]["status"] == "blocked", f"{o['status']}, pick {o['pick_ticket']['status']}")
check(all(v["reasons"] for v in o["fields"].values() if v["status"] != "verified"), "every non-green field has a reason")

step("6. extra: RX-006 NPI transposition + look-alike; RX-011 three drugs; RX-016 denied; RX-019 Schedule II")
o = run("RX-006")[0]
n = fx(o, "npi")
check(n["status"] in ("conflict", "review") and (("1987654328" in n["candidates"]) or any("1987654328" in r for r in n["reasons"])),
      f"NPI suggests 1987654328: cands {n['candidates']} ")
check(set(fx(o, "drug")["candidates"]) == {"clonidine", "clonazepam"}, f"look-alike {fx(o, 'drug')['candidates']}")
os3 = run("RX-011")
check(len(os3) == 3 and len({x["fax_id"] for x in os3}) == 1 and all(x["status"] == "ready_to_fill" for x in os3), f"3 orders, one fax_id: {[x['fields']['drug']['value'] for x in os3]}")
check(all(x["pick_ticket"]["status"] == "in_stock" for x in os3), "3 pick tickets")
o = run("RX-016")[0]
check(o["status"] == "denied_by_prescriber" and o["callback_draft"], f"{o['status']}: {o['callback_draft'][:60]}")
o = run("RX-019")[0]
check(o["status"] == "needs_pharmacist" and any("Schedule II" in ch["detail"] for ch in fx(o, "drug")["checks"]), "oxycodone by fax held for pharmacist")

step("7. judge moments: photo upload, duplicate resend, tiny image, bad file, clear")
with open("samples/faxes/RX-017_fax.png", "rb") as f:
    r = c.post(f"{B}/faxes/upload", files={"file": ("photo.png", f, "image/png")})
check(r.status_code == 200 and r.json()[0]["status"] == "ready_to_fill", f"phone photo upload -> {r.json()[0]['status'] if r.status_code == 200 else r.text[:80]}")
check(any("perspective" in a["detail"] for a in r.json()[0]["audit"]), "audit shows perspective correction")
with open("samples/faxes/RX-017_fax.png", "rb") as f:
    r2 = c.post(f"{B}/faxes/upload", files={"file": ("photo_again.png", f, "image/png")}).json()[0]
check(r2["status"] == "needs_pharmacist" and any("duplicate" in x for x in fx(r2, "patient_name")["reasons"]), f"same photo again -> {r2['status']}: {fx(r2, 'patient_name')['reasons'][0][:60]}")
with open("samples/robustness/J11_tiny_low_res.png", "rb") as f:
    r3 = c.post(f"{B}/faxes/upload", files={"file": ("tiny.png", f, "image/png")}).json()[0]
check(r3["status"] == "needs_pharmacist" and "resolution" in r3["pick_ticket"]["warnings"][0], "tiny image fails safe")
r4 = c.post(f"{B}/faxes/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
check(r4.status_code == 400, f"bad file type -> {r4.status_code}")
check(c.post(f"{B}/orders/clear").json()["cleared"] > 0, "clear all")

print("\n" + ("ALL GOOD: demo flow passes end to end" if not fails else f"{len(fails)} FAILURES:\n - " + "\n - ".join(fails)))
