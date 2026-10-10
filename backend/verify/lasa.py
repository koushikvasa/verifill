"""
Lane: Backend
Look-alike / sound-alike guard (ISMP pairs in lasa_pairs.json).

Flags (status conflict, candidates=[...]) when the drug reading is NOT clean and either:
- a reader's value is a LASA pair member, or fuzzy-close to one, or
- a partial reading ("Hy????yzine") matches a known drug,
- or the readers disagree between drugs.
A clean, unanimous, OCR-confirmed reading of a pair member (e.g. RX-001 lisinopril) passes
with a note, otherwise every lisinopril Rx would be red.

The sig's indication words (drugs.json "indications") are shown as EVIDENCE to the
pharmacist, never as an automatic decision.
RULE: inventory must never be used to pick between candidates.
"""
import json
import re

from rapidfuzz import fuzz

from backend import config
from backend.models import Check
from backend.verify.rxnorm import DRUGS

PAIRS = json.loads((config.DATA_DIR / "lasa_pairs.json").read_text())["pairs"]
ALL_NAMES = sorted(set(DRUGS["drugs"]) | {n for p in PAIRS for n in p})


def partners(drug: str) -> list[str]:
    d = drug.lower()
    return [b if a == d else a for a, b in PAIRS if d in (a, b)]


def matches(reading: str) -> list[str]:
    """Known drug names this reading could be. '?' = one unreadable character."""
    r = reading.lower().strip(" .,")
    r = DRUGS["brands"].get(r, r)
    if "?" in r:
        pat = re.compile("^" + "".join("." if ch == "?" else re.escape(ch) for ch in r) + "$")
        return [n for n in ALL_NAMES if pat.match(n)]
    if r in ALL_NAMES:
        return [r]
    return [n for n in ALL_NAMES if fuzz.ratio(r, n) >= 80]


def check(readings: list[str], clean: bool, sig: str | None) -> tuple[Check | None, list[str], list[str]]:
    """-> (check or None, candidates, evidence lines)."""
    hits: list[str] = []
    for r in readings:
        for m in matches(r):
            for n in [m, *partners(m)]:
                if n not in hits:
                    hits.append(n)
    risky = [n for n in hits if partners(n)]
    if not hits:
        if readings:
            shown = ", ".join(repr(r) for r in readings)
            return (Check(name="lasa", passed=False, grounded_in="local",
                          detail=f"{shown} does not match any drug in the reference; cannot identify the drug"),
                    [], [])
        return None, [], []
    if clean and len(hits) <= 2 and len(readings) == 1 and readings[0].lower() in hits:
        note = (f"{readings[0]} is on the ISMP look-alike list ({', '.join(partners(readings[0].lower()))}); "
                f"reading was unanimous and OCR-confirmed") if risky else f"{readings[0]}: no look-alike risk listed"
        return Check(name="lasa", passed=True, grounded_in="local", detail=note), [], []
    if not risky and len(hits) == 1:
        return None, [], []                       # unclear handwriting, but only one possible drug
    detail = "look-alike risk: " + " vs ".join(hits) + " (handwriting is not clean enough to tell them apart)"
    return Check(name="lasa", passed=False, grounded_in="local", detail=detail), hits, indication_evidence(hits, sig)


def indication_evidence(cands: list[str], sig: str | None) -> list[str]:
    if not sig:
        return []
    s = sig.lower()
    out = []
    for c in cands:
        for ind in DRUGS["drugs"].get(c, {}).get("indications", []):
            if re.search(rf"\b{re.escape(ind)}\b", s):
                out.append(f"evidence only: sig says '{ind}', an indication for {c}; confirm with prescriber or pharmacist")
    return out
