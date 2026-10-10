"""
Lane: Backend
Drug + strength grounding. Order of truth: PRISM Knowledge Base (drugs.json
uploaded there) -> RxNav live (when not OFFLINE) -> local drugs.json.

TODO:
- normalize(drug_text): lowercase, map brands (drugs.json "brands"), rapidfuzz to known names
- strength_exists(drug, strength_mg) -> Check
- rxnav: GET {RXNAV_URL}/approximateTerm.json?term=<drug strength form>&maxEntries=3
         GET {RXNAV_URL}/rxcui/<rxcui>/ndcs.json  (for inventory NDC matching)
"""
import json

from backend import config

DRUGS = json.loads((config.DATA_DIR / "drugs.json").read_text())


def known(drug: str) -> dict | None:
    d = drug.lower().strip()
    d = DRUGS["brands"].get(d, d)
    return DRUGS["drugs"].get(d)
