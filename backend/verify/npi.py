"""
Lane: Backend
Prescriber verification: NPI check digit (CMS Luhn with 80840 prefix) +
registry match (PRISM Knowledge Base / NPPES live / local prescribers.json).
"""
import json

from backend import config


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


def lookup(npi: str) -> dict | None:
    """TODO: query PRISM KB first, then NPPES when not OFFLINE."""
    reg = json.loads((config.DATA_DIR / "prescribers.json").read_text())["prescribers"]
    return reg.get(npi)


def transposition_candidates(npi: str) -> list[str]:
    """Valid NPIs one adjacent swap away (catches 1987654382 -> 1987654328)."""
    out = []
    for i in range(len(npi) - 1):
        s = list(npi); s[i], s[i + 1] = s[i + 1], s[i]
        c = "".join(s)
        if c != npi and luhn_ok(c):
            out.append(c)
    return out
