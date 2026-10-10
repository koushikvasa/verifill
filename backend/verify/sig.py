"""
Lane: Backend
Prescriber shorthand -> structured dose, for the dose/qty rules and a human-readable
line in the UI. The raw sig is never changed; this is a parallel reading shown next to it.

parse("ii po bid x 30 d") -> {"tabs_per_dose": 2, "doses_per_day": 2, "prn": False, "days": 30,
                              "human_readable": "Take 2 tablets by mouth twice daily for 30 days"}
Returns None when the frequency can't be understood (ud = as directed, free text...).
"""
import re

ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "ss": 0.5, "½": 0.5, "one": 1, "two": 2, "three": 3, "half": 0.5}
UNITS = {"tab": "tablet", "tabs": "tablet", "tablet": "tablet", "tablets": "tablet", "t": "tablet",
         "cap": "capsule", "caps": "capsule", "capsule": "capsule", "capsules": "capsule",
         "ml": "mL", "mls": "mL", "tsp": "teaspoon", "puff": "puff", "puffs": "puff", "gtt": "drop", "gtts": "drop",
         "drop": "drop", "drops": "drop", "unit": "unit", "units": "unit", "patch": "patch"}
ROUTES = [(r"\bp\.?o\.?\b|by mouth|orally", "by mouth"), (r"\bs\.?l\.?\b|sublingual", "under the tongue"),
          (r"\btop\b|topical(ly)?", "topically"), (r"\bp\.?r\.?\b|rectal(ly)?", "rectally"),
          (r"\bsubq\b|\bsc\b|\bsq\b|subcutaneous(ly)?", "subcutaneously"), (r"\bim\b|intramuscular(ly)?", "intramuscularly"),
          (r"\binh\b|inhale[d]?", "by inhalation"), (r"\bou\b", "in both eyes"), (r"\bod\b", "in the right eye"),
          (r"\bos\b", "in the left eye")]
FREQ = [(r"\bq\.?\s?4\s?h(ours?|rs?)?\b|every 4 hours", 6, "every 4 hours"),
        (r"\bq\.?\s?6\s?h(ours?|rs?)?\b|every 6 hours", 4, "every 6 hours"),
        (r"\bq\.?\s?8\s?h(ours?|rs?)?\b|every 8 hours", 3, "every 8 hours"),
        (r"\bq\.?\s?12\s?h(ours?|rs?)?\b|every 12 hours", 2, "every 12 hours"),
        (r"\bq\.?i\.?d\.?\b|four times (a |per )?day|4x/?day", 4, "four times daily"),
        (r"\bt\.?i\.?d\.?\b|three times (a |per )?day|3x/?day", 3, "three times daily"),
        (r"\bb\.?i\.?d\.?\b|twice (a |per )?day|2x/?day|two times (a |per )?day", 2, "twice daily"),
        (r"\bq\.?o\.?d\.?\b|every other day", 0.5, "every other day"),
        (r"\bq\.?\s?w(ee)?k(ly)?\b|once (a |per )?week|weekly", 1 / 7, "once weekly"),
        (r"\bq\.?h\.?s\.?\b|at bedtime|at night|nightly", 1, "at bedtime"),
        (r"\bq\.?a\.?m\.?\b|every morning|in the morning", 1, "every morning"),
        (r"\bq\.?p\.?m\.?\b|every evening|in the evening", 1, "every evening"),
        (r"\bq\.?d\.?\b|\bdaily\b|once (a |per )?day|every day|\bod\b(?! eye)|1x/?day|per day", 1, "once daily")]


def parse(sig: str | None) -> dict | None:
    if not sig:
        return None
    s = " " + re.sub(r"\s+", " ", sig.lower().strip()) + " "
    if re.search(r"\bu\.?d\.?\b|as directed|per (md|dr|physician)", s) and not re.search(r"\d|\bi+\b", s):
        return None

    m = re.search(r"(?:take|give|use|apply|instill|inject)?\s*(\d+(?:\.\d+)?|½|\bi{1,3}\b|\biv\b|\bss\b|one|two|three|half)"
                  r"(?:\s*(?:-|to)\s*(\d+(?:\.\d+)?|\bi{1,3}\b))?\s*"
                  r"(tabs?|tablets?|caps?|capsules?|mls?|tsp|puffs?|gtts?|drops?|units?|patch|t)?\b", s)
    if not m:
        return None
    lo = ROMAN.get(m.group(1), None)
    lo = float(m.group(1)) if lo is None and re.match(r"[\d.½]", m.group(1)) and m.group(1) != "½" else lo
    if lo is None:
        return None
    hi = m.group(2)
    hi = ROMAN.get(hi, float(hi) if hi and re.match(r"\d", hi) else None) if hi else None
    tabs = max(lo, hi or lo)                           # ranges: use the max for safety checks
    unit = UNITS.get(m.group(3) or "", "tablet" if m.group(3) is None else m.group(3))

    freq = next(((n, text) for pat, n, text in FREQ if re.search(pat, s)), None)
    if freq is None:
        return None
    per_day, freq_text = freq
    route = next((text for pat, text in ROUTES if re.search(pat, s)), None)
    prn = bool(re.search(r"\bprn\b|as needed|if needed|when necessary", s))
    dm = re.search(r"(?:x|for|×)\s*(\d+)\s*(?:d\b|days?\b|wks?\b|weeks?\b)", s)
    days = int(dm.group(1)) * (7 if dm and re.search(r"w", dm.group(0)) else 1) if dm else None
    reason = re.search(r"\b(?:for|c|with)\s+([a-z ]{3,30}?)(?=\s*(?:$|x\s*\d|prn|\d))", s)

    qty_txt = f"{tabs:g} {unit}{'s' if tabs != 1 and unit not in ('mL',) else ''}"
    hr = f"Take {qty_txt}" + (f" {route}" if route else "") + f" {freq_text}"
    if prn:
        hr += ", as needed"
    if days:
        hr += f" for {days} days"
    if reason and reason.group(1).strip() not in ("meals", "food", "water"):
        hr += f" ({reason.group(1).strip()})"
    elif reason:
        hr += f" with {reason.group(1).strip()}"
    return {"tabs_per_dose": tabs, "doses_per_day": per_day, "prn": prn, "days": days, "unit": unit,
            "human_readable": hr}
