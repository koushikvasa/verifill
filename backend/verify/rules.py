"""
Lane: Backend
Cross-field logic checks. A field can be read perfectly and still be wrong.
All deterministic; each returns a Check (or None when its inputs aren't available).

- max_daily_dose: tabs/dose x doses/day x strength <= drugs.json max_daily_mg
- decimal_error: strength is 10x or 0.1x a marketed strength (0.5 -> 0.05 levothyroxine)
- qty_days: quantity ~= tabs/day x days_supply (+-10%)
- qty_words: "#30 (thirty)" numeral and words agree
- dates: date_written not in the future and not > 1 year old; DOB plausible
- refills: <= 11 (a non-controlled Rx is valid for a year)
"""
import re
from datetime import date, datetime

from backend.models import Check
from backend.verify.rxnorm import DRUGS, canonical, parse_mg

FREQ = [(r"\bq\.?\s?4\s?h", 6), (r"\bq\.?\s?6\s?h", 4), (r"\bq\.?\s?8\s?h", 3), (r"\bq\.?\s?12\s?h", 2),
        (r"\bq\.?i\.?d\b|four times", 4), (r"\bt\.?i\.?d\b|three times", 3), (r"\bb\.?i\.?d\b|twice", 2),
        (r"\bq\.?d\b|daily|once a day|every day|\bqhs\b|at bedtime|before breakfast|\bqam\b|\bqpm\b", 1)]
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45,
         "fifty": 50, "sixty": 60, "ninety": 90, "one hundred": 100, "one hundred twenty": 120, "hundred": 100,
         "one hundred eighty": 180}


def per_day(sig: str | None) -> tuple[float, float] | None:
    """'1 tab PO BID' -> (1 tab/dose, 2 doses/day). PRN uses its max frequency. None if unclear."""
    if not sig:
        return None
    s = sig.lower()
    m = re.match(r"\s*(?:take\s+)?(\d+(?:\.\d+)?|one|two|half|½)\s*(?:tabs?|tablets?|caps?|capsules?)\b", s)
    if not m:
        return None
    tabs = {"one": 1, "two": 2, "half": 0.5, "½": 0.5}.get(m.group(1)) or float(m.group(1))
    for pat, n in FREQ:
        if re.search(pat, s):
            return tabs, n
    return None


def number(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def max_daily_dose(drug: str, strength: str, sig: str) -> Check | None:
    c, mg, pd = canonical(drug), parse_mg(strength), per_day(sig)
    if not (c and mg is not None and pd):
        return None
    total, mx = pd[0] * pd[1] * mg, DRUGS["drugs"][c]["max_daily_mg"]
    ok = total <= mx + 1e-9
    return Check(name="max_daily_dose", passed=ok, grounded_in="local",
                 detail=f"{total:g} mg/day ({pd[0]:g} x {pd[1]:g}/day x {mg:g} mg) "
                        + ("is within" if ok else "exceeds") + f" max daily {mx:g} mg for {c}")


def decimal_error(drug: str, strength: str) -> Check | None:
    c, mg = canonical(drug), parse_mg(strength)
    if not (c and mg):
        return None
    marketed = DRUGS["drugs"][c]["strengths_mg"]
    if any(abs(mg - s) < 1e-9 for s in marketed):
        return None
    for factor in (10, 0.1, 100, 0.01):
        hit = next((s for s in marketed if abs(mg / factor - s) < 1e-9), None)
        if hit is not None:
            mcg = f" ({hit * 1000:g} mcg)" if hit < 1 else ""
            return Check(name="decimal_error", passed=False, grounded_in="local",
                         detail=f"{strength} is {factor:g}x the marketed {hit:g} mg{mcg}: likely decimal error; "
                                f"callback to prescriber required")
    return None


def qty_days(quantity: str, days: str, sig: str) -> Check | None:
    q, d, pd = number(quantity), number(days), per_day(sig)
    if not (q and d and pd):
        return None
    expected = pd[0] * pd[1] * d
    ok = abs(q - expected) <= 0.1 * expected
    return Check(name="qty_days", passed=ok, grounded_in="local",
                 detail=f"quantity {q} vs {pd[0]:g} x {pd[1]:g}/day x {d} days = {expected:g}"
                        + ("" if ok else ": mismatch"))


def qty_words(quantity: str) -> Check | None:
    m = re.search(r"\(([a-z -]+)\)", quantity.lower())
    n = number(quantity)
    if not (m and n):
        return None
    w = WORDS.get(m.group(1).strip())
    if w is None:
        return None
    return Check(name="qty_words", passed=w == n, grounded_in="local",
                 detail=f"numeral {n} and words '{m.group(1)}' " + ("agree" if w == n else "DISAGREE"))


def _date(s: str | None) -> date | None:
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


def date_written(s: str, today: date | None = None) -> Check | None:
    d, today = _date(s), today or date.today()
    if d is None:
        return Check(name="date_written", passed=False, grounded_in="local", detail=f"cannot read {s!r} as a date")
    if d > today:
        return Check(name="date_written", passed=False, grounded_in="local", detail=f"date written {s} is in the future")
    if (today - d).days > 365:
        return Check(name="date_written", passed=False, grounded_in="local", detail=f"date written {s} is over a year old")
    return Check(name="date_written", passed=True, grounded_in="local", detail=f"date written {s} is current")


def dob(s: str, today: date | None = None) -> Check | None:
    d, today = _date(s), today or date.today()
    if d is None:
        return Check(name="dob", passed=False, grounded_in="local", detail=f"cannot read {s!r} as a date")
    age = (today - d).days / 365.25
    ok = 0 <= age <= 120
    return Check(name="dob", passed=ok, grounded_in="local",
                 detail=f"DOB {s} (age {age:.0f})" + ("" if ok else " is not plausible"))


def refills(s: str) -> Check | None:
    n = number(s)
    if n is None:
        return None
    ok = n <= 11
    return Check(name="refills", passed=ok, grounded_in="local",
                 detail=f"{n} refills" + ("" if ok else " exceeds the 1-year limit (11)"))
