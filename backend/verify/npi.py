"""
Lane: Backend
Prescriber verification (safety layer L4):
1. NPI check digit (CMS Luhn with 80840 prefix); failure -> suggest adjacent transpositions.
2. Registry lookup: PRISM Knowledge Base -> local prescribers.json -> NPPES live (not OFFLINE).
3. NPPES record: active, individual (NPI-1), prescriber-type taxonomy.
4. NAME MATCH: the registry name for this NPI must match the prescriber name on the fax.
5. NPI missing: NPPES/registry search by name -> suggestions in `candidates`, NEVER the value.
NPPES unreachable -> "unverified" (amber), never green.
"""
import json
import re
from functools import lru_cache

import httpx
from rapidfuzz import fuzz

from backend import config, tracing
from backend.models import Check

REGISTRY = json.loads((config.DATA_DIR / "prescribers.json").read_text())["prescribers"]
TITLES = {"dr", "doctor", "md", "do", "np", "pa", "pa-c", "aprn", "fnp", "dnp", "rn", "dds", "dmd", "dpm",
          "od", "phd", "mbbs", "jr", "sr", "ii", "iii"}
PRESCRIBER_TAXONOMY = re.compile(r"physician|allopathic|osteopathic|medicine|nurse practitioner|physician assistant|"
                                 r"dentist|podiatrist|optometrist|surgery|pediatrics|psychiatry|family|internal",
                                 re.I)


class Unreachable(Exception):
    pass


def luhn_ok(npi: str) -> bool:
    if not (npi and npi.isdigit() and len(npi) == 10):
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + npi[:9])):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return str((10 - total % 10) % 10) == npi[9]


def transposition_candidates(npi: str) -> list[str]:
    """Valid NPIs one adjacent swap away (catches 1987654382 -> 1987654328)."""
    out = []
    for i in range(len(npi) - 1):
        s = list(npi); s[i], s[i + 1] = s[i + 1], s[i]
        c = "".join(s)
        if c != npi and luhn_ok(c):
            out.append(c)
    return out


# ---------- registry ----------

@lru_cache(maxsize=256)
def _kb(npi: str) -> dict | None:
    for hit in tracing.kb_search(f"PRESCRIBER NPI {npi}", limit=3):
        if hit.get("doc_name") == f"prescriber_{npi}.txt":
            m = re.match(r"PRESCRIBER NPI \d+: (.+?), (\w+), (.+?), state (\w+), phone (.+?), fax (.+?)\.", hit["content"])
            if m:
                return {"name": m.group(1), "credential": m.group(2), "specialty": m.group(3),
                        "state": m.group(4), "phone": m.group(5), "fax": m.group(6), "active": True}
    return None


@lru_cache(maxsize=256)
def _nppes(npi: str) -> dict | None:
    """NPPES record (normalized) or None if not enumerated. Raises Unreachable on network error."""
    try:
        r = httpx.get(config.NPPES_URL, params={"version": "2.1", "number": npi}, timeout=5)
        r.raise_for_status()
        res = r.json().get("results") or []
    except (httpx.HTTPError, ValueError) as e:
        raise Unreachable(str(e)) from e
    return _from_nppes(res[0]) if res else None


def _from_nppes(rec: dict) -> dict:
    b = rec.get("basic", {})
    tax = next((t for t in rec.get("taxonomies", []) if t.get("primary")), (rec.get("taxonomies") or [{}])[0])
    loc = next((a for a in rec.get("addresses", []) if a.get("address_purpose") == "LOCATION"), {})
    return {"npi": rec.get("number"),
            "name": " ".join(x for x in [b.get("first_name"), b.get("middle_name"), b.get("last_name")] if x)
                    or b.get("organization_name", ""),
            "credential": b.get("credential", ""), "specialty": tax.get("desc", ""), "state": loc.get("state", ""),
            "phone": loc.get("telephone_number", ""), "fax": loc.get("fax_number", ""),
            "active": b.get("status", "A") == "A", "individual": rec.get("enumeration_type") == "NPI-1"}


def lookup(npi: str) -> tuple[dict | None, str]:
    """-> (record, grounded_in). Raises Unreachable only if every source failed to answer."""
    rec = _kb(npi)
    if rec:
        return rec, "prism_kb"
    if npi in REGISTRY:
        return {**REGISTRY[npi], "active": True, "individual": True}, "local"
    if config.OFFLINE:
        return None, "local"
    return _nppes(npi), "nppes"


def search_by_name(name: str, state: str | None = None) -> list[dict]:
    """Candidate prescribers for a written name (missing-NPI flow). Suggestions only."""
    first, last = split_name(name)
    if not last:
        return []
    out = [{"npi": n, **p} for n, p in REGISTRY.items() if names_match(p["name"], name)[0]]
    if not out and not config.OFFLINE:
        try:
            params = {"version": "2.1", "last_name": last, "enumeration_type": "NPI-1", "limit": 20}
            if first:
                params["first_name"] = first
            if state:
                params["state"] = state
            r = httpx.get(config.NPPES_URL, params=params, timeout=5)
            out = [_from_nppes(x) for x in r.json().get("results") or []]
        except (httpx.HTTPError, ValueError):
            return []
    return out


# ---------- names ----------

def split_name(name: str) -> tuple[str, str]:
    """'Dr. Maya Okafor, MD' -> ('maya', 'okafor'); 'Okafor, Maya' -> ('maya', 'okafor')."""
    s = name.lower()
    parts = [p.strip() for p in s.split(",")]
    if len(parts) >= 2 and parts[1] and not set(re.findall(r"[a-z-]+", parts[1])) <= TITLES:
        s = parts[1] + " " + parts[0]                 # "Last, First" form
    toks = [t for t in re.findall(r"[a-z][a-z'-]*", s) if t.strip(".") not in TITLES]
    toks = [t for t in toks if len(t) > 1 or toks.index(t) == 0]   # drop middle initials
    if not toks:
        return "", ""
    return (toks[0] if len(toks) > 1 else ""), toks[-1]


def names_match(registry_name: str, written: str) -> tuple[bool, str]:
    rf, rl = split_name(registry_name)
    wf, wl = split_name(written)
    if not wl:
        return False, "no prescriber name to compare"
    last_ok = fuzz.ratio(rl, wl) >= 90
    first_ok = (not wf or not rf or wf[0] == rf[0]) if len(wf) <= 1 else fuzz.ratio(rf, wf) >= 85
    return last_ok and first_ok, f"registry: {registry_name}; fax: {written}"


# ---------- checks ----------

def checks_for(npi_text: str, written_name: str | None) -> tuple[list[Check], list[str], list[str]]:
    """-> (checks, candidates, unverified-notes) for one NPI value as written."""
    npi = re.sub(r"\D", "", npi_text)
    checks: list[Check] = []
    if not luhn_ok(npi):
        sugg = transposition_candidates(npi) if len(npi) == 10 else []
        hint = f"; likely transposed: {', '.join(sugg)}" if sugg else ""
        checks.append(Check(name="npi_luhn", passed=False, grounded_in="local",
                            detail=f"NPI {npi_text} fails the check digit{hint}"))
        return checks, sugg, []
    checks.append(Check(name="npi_luhn", passed=True, grounded_in="local", detail=f"NPI {npi} check digit valid"))
    try:
        rec, src = lookup(npi)
    except Unreachable as e:
        return checks, [], [f"unverified: NPPES registry unreachable ({str(e)[:60]}); NPI not confirmed"]
    if rec is None:
        where = "local registry (offline mode)" if src == "local" else "NPPES"
        checks.append(Check(name="npi_registry", passed=False, grounded_in=src,
                            detail=f"NPI {npi} passes the check digit but is not found in {where}"))
        return checks, [], []
    checks.append(Check(name="npi_registry", passed=True, grounded_in=src,
                        detail=f"NPI {npi} registered to {rec['name']} ({rec.get('specialty', '')}, {rec.get('state', '')})"))
    if not rec.get("active", True):
        checks.append(Check(name="npi_active", passed=False, grounded_in=src, detail=f"NPI {npi} is deactivated"))
    if not rec.get("individual", True):
        checks.append(Check(name="npi_individual", passed=False, grounded_in=src,
                            detail=f"NPI {npi} belongs to an organization, not an individual prescriber"))
    if src == "nppes" and rec.get("specialty") and not PRESCRIBER_TAXONOMY.search(rec["specialty"]):
        checks.append(Check(name="npi_taxonomy", passed=False, grounded_in=src,
                            detail=f"taxonomy '{rec['specialty']}' is not a prescriber type"))
    if written_name:
        ok, detail = names_match(rec["name"], written_name)
        checks.append(Check(name="prescriber_name_match", passed=ok, grounded_in=src,
                            detail=(f"name matches registry ({detail})" if ok else
                                    f"NPI {npi} is registered to {rec['name']}; fax says {written_name}")))
    else:
        return checks, [], ["unverified: prescriber name unreadable, so NPI owner could not be matched"]
    return checks, [], []


def registry_record(npi: str) -> dict | None:
    """For the callback drafter: phone/fax of the prescriber (None if unknown)."""
    try:
        return lookup(re.sub(r"\D", "", npi))[0]
    except Unreachable:
        return None
