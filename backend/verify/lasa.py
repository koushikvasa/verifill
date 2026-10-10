"""
Lane: Backend
Look-alike / sound-alike guard (ISMP pairs in lasa_pairs.json).

TODO: if extracted drug is in a pair, OR fuzzy-close to a pair member, OR the
readers disagree between two pair members -> status conflict, candidates=[both],
and use the sig's indication words (drugs.json "indications") as EVIDENCE shown
to the pharmacist, never as an automatic decision.
RULE: inventory must never be used to pick between candidates.
"""
import json

from backend import config

PAIRS = json.loads((config.DATA_DIR / "lasa_pairs.json").read_text())["pairs"]


def partners(drug: str) -> list[str]:
    d = drug.lower()
    return [b if a == d else a for a, b in PAIRS if d in (a, b)]
