"""
Lane: Backend
Drug + strength + form grounding. Order of truth: PRISM Knowledge Base via
tracing.kb_search() (data uploaded by scripts/upload_kb.py) -> local drugs.json.
RxNav live (when not OFFLINE) is attached as EVIDENCE only: RxNav "knows" a veterinary
levothyroxine 0.5 mg (Thyro-Tabs), so existence there must never pass a dose.
Check.grounded_in says which source confirmed the fact ("prism_kb" | "local" | "rxnav").
"""
import json
import re
from functools import lru_cache

import httpx

from backend import config, tracing
from backend.models import Check

DRUGS = json.loads((config.DATA_DIR / "drugs.json").read_text())


def canonical(drug: str | None) -> str | None:
    """'Lisinopril' -> 'lisinopril', 'Zoloft' -> 'sertraline'. Exact names only: no fuzzy
    guessing here (fuzzy candidates are lasa.py's job, shown to the pharmacist)."""
    if not drug or "?" in drug:
        return None
    d = re.sub(r"\s+", " ", drug.lower().strip(" .,"))
    d = DRUGS["brands"].get(d, d)
    return d if d in DRUGS["drugs"] else None


def known(drug: str) -> dict | None:
    c = canonical(drug)
    return DRUGS["drugs"].get(c) if c else None


def parse_mg(strength: str | None) -> float | None:
    """'0.5 mg' -> 0.5, '50 mcg' -> 0.05, '10mg' -> 10. None if not a simple mg/mcg strength."""
    if not strength:
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?|\.\d+)\s*(mg|mcg|µg|ug)\s*", strength.lower())
    if not m:
        return None
    v = float(m.group(1))
    return v / 1000 if m.group(2) in ("mcg", "µg", "ug") else v


@lru_cache(maxsize=256)
def kb_fact(drug: str) -> str | None:
    """The PRISM KB chunk for this drug (exact doc match), or None if KB unavailable."""
    for hit in tracing.kb_search(f"DRUG {drug} marketed strengths forms max daily dose", limit=3):
        if hit.get("doc_name") == f"drug_{drug.replace(' ', '_')}.txt":
            return hit.get("content")
    return None


def _source(drug: str, fact: str) -> str:
    """'prism_kb' if the KB chunk states this fact, else 'local'."""
    content = kb_fact(drug)
    return "prism_kb" if content and fact.lower() in content.lower() else "local"


def drug_exists(drug_text: str) -> Check:
    c = canonical(drug_text)
    if c is None:
        return Check(name="drug_reference", passed=False, grounded_in="local",
                     detail=f"{drug_text!r} is not in the drug reference; cannot verify it")
    brand = f" (brand {drug_text} = {c})" if c != drug_text.lower().strip() else ""
    return Check(name="drug_reference", passed=True, grounded_in=_source(c, f"DRUG {c}"),
                 detail=f"{c} found in drug reference{brand}")


def strength_marketed(drug_text: str, strength: str) -> Check:
    c, mg = canonical(drug_text), parse_mg(strength)
    info = DRUGS["drugs"][c]
    if mg is None:
        return Check(name="strength_marketed", passed=False, grounded_in="local",
                     detail=f"could not read {strength!r} as a mg/mcg strength")
    ok = any(abs(mg - s) < 1e-9 for s in info["strengths_mg"])
    marketed = ", ".join(f"{s:g}" for s in info["strengths_mg"])
    return Check(name="strength_marketed", passed=ok, grounded_in=_source(c, f"{mg:g}" if ok else marketed),
                 detail=(f"{strength} is a marketed {c} strength" if ok else
                         f"{strength} is not a marketed {c} strength (marketed mg: {marketed})"))


def form_marketed(drug_text: str, form: str) -> Check:
    c = canonical(drug_text)
    forms = DRUGS["drugs"][c]["forms"]
    f = re.sub(r"^oral\s+", "", form.lower().strip()).replace("(s)", "").rstrip("s")
    ok = f in forms
    return Check(name="form_marketed", passed=ok, grounded_in=_source(c, forms[0]),
                 detail=f"{form} {'is' if ok else 'is not'} an available form of {c} ({', '.join(forms)})")


@lru_cache(maxsize=128)
def rxnav_evidence(drug: str, strength: str) -> str | None:
    """Live RxNav lookup, evidence only (never decides). None when OFFLINE or unreachable."""
    if config.OFFLINE:
        return None
    try:
        r = httpx.get(f"{config.RXNAV_URL}/approximateTerm.json",
                      params={"term": f"{drug} {strength}", "maxEntries": 3}, timeout=4)
        cands = r.json().get("approximateGroup", {}).get("candidate", [])
        names = list(dict.fromkeys(c.get("name") for c in cands if c.get("name")))
        return f"RxNav closest products: {'; '.join(names[:3])}" if names else "RxNav: no matching product"
    except (httpx.HTTPError, ValueError):
        return None


def query_kb(drug: str) -> str | None:
    """Hook kept for the team: KB text for a drug (None if PRISM KB unavailable)."""
    c = canonical(drug)
    return kb_fact(c) if c else None
