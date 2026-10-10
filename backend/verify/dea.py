"""
Lane: Backend (A9)
DEA registration number check, for controlled substances only.
Format: 2 letters + 7 digits. First letter = registrant type (A/B/F/G practitioner, M mid-level,
P/R distributor, X suboxone...). Second letter = first letter of the registrant's last name
(or 9 for a mid-level practitioner using a supervisor's number). Check digit:
(d1+d3+d5) + 2*(d2+d4+d6), last digit must equal d7.
Deterministic; evidence for the pharmacist, grounded_in="local".
"""
import re

from backend.models import Check
from backend.verify.npi import split_name

DEA_RE = re.compile(r"\b([ABCDEFGHJKLMPRSTUX])([A-Z9])\s?(\d{7})\b")
FIRST_LETTERS = set("ABCDEFGHJKLMPRSTUX")


def find(text: str) -> list[str]:
    """DEA-looking numbers in page text (OCR + grounding OCR)."""
    return [f"{a}{b}{d}" for a, b, d in DEA_RE.findall(text.upper())]


def check_digit_ok(dea: str) -> bool:
    d = dea[2:]
    if not (len(d) == 7 and d.isdigit()):
        return False
    total = int(d[0]) + int(d[2]) + int(d[4]) + 2 * (int(d[1]) + int(d[3]) + int(d[5]))
    return total % 10 == int(d[6])


def validate(dea: str, prescriber_name: str | None) -> Check:
    dea = dea.upper().replace(" ", "")
    if not (len(dea) == 9 and dea[0] in FIRST_LETTERS and (dea[1].isalpha() or dea[1] == "9") and dea[2:].isdigit()):
        return Check(name="dea_format", passed=False, grounded_in="local", detail=f"DEA {dea} is not a valid format")
    if not check_digit_ok(dea):
        return Check(name="dea_check_digit", passed=False, grounded_in="local",
                     detail=f"DEA {dea} fails its check digit; verify with the prescriber")
    if prescriber_name and dea[1] != "9":
        _, last = split_name(prescriber_name)
        if last and last[0].upper() != dea[1]:
            return Check(name="dea_name_letter", passed=False, grounded_in="local",
                         detail=f"DEA {dea}: second letter {dea[1]} does not match prescriber last name "
                                f"{last.title()!r}; may belong to someone else")
    return Check(name="dea", passed=True, grounded_in="local", detail=f"DEA {dea} format, check digit and name letter valid")


def missing() -> Check:
    return Check(name="dea_missing", passed=False, grounded_in="local",
                 detail="controlled substance: no DEA number found on the prescription; required before dispensing")
