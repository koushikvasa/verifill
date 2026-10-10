"""
Lane: Backend
Turns readings + checks into one value and a color per field, with reasons.

Sources: "ocr" (Tesseract, weak vote), "reader_a:<model>", "reader_b:<model>" (vision LLMs,
different vendors). Values are NORMALIZED ONLY FOR COMPARISON; the value shown is always
a reader's raw transcription (never auto-corrected).

GREEN (verified): >=2 independent sources agree AND the agreeing readers marked it legible
    AND (pixel grounding) the value is supported by the OCR text when OCR read the field
    AND every check passed.
AMBER (review): only one source, readers unsure, nobody could read it, or the value was
    never seen by OCR ("model-only").
RED (conflict): the two vision readers disagree, OCR shows a decimal/zero variant of the
    agreed value (possible silent correction), any check failed, or look-alike candidates exist.

Order -> ready_to_fill only if every CRITICAL_FIELD is verified/confirmed and there are no
guardrail flags. Every amber/red field gets at least one human-readable reason.
PRISM Evaluator: attached to the `verifill-score` agent's output in the dashboard.
"""
import re

from rapidfuzz import fuzz

from backend import config
from backend.models import CRITICAL_FIELDS, ExtractedField, FieldName, Order, OrderStatus, Status

# Fields where a near-miss is a different value (0.5 vs 0.05 mg, 1 vs 7 tabs): exact match only.
EXACT = {FieldName.dob, FieldName.date_written, FieldName.drug, FieldName.strength, FieldName.dosage_form,
         FieldName.sig, FieldName.quantity, FieldName.days_supply, FieldName.refills, FieldName.daw,
         FieldName.npi}
GROUND_RATIO = 80        # OCR is noisy on handwriting: ~80% character similarity counts as "seen"
_WORD_NUMS = r"\((?:[a-z -]+)\)"   # "(thirty)" spelled-out quantity


def normalize(field: FieldName, v: str | None) -> str | None:
    """Comparison key only. Never shown to the user and never written back to the field."""
    if v is None:
        return None
    s = str(v).lower().strip()
    s = re.sub(r"\s+", " ", s)
    if field == FieldName.daw:
        return {"true": "true", "yes": "true", "checked": "true", "false": "false", "no": "false",
                "unchecked": "false"}.get(s, s)
    if field in (FieldName.quantity, FieldName.days_supply, FieldName.refills):
        s = re.sub(_WORD_NUMS, "", s).lstrip("#").strip()
        s = re.sub(r"\s*(tabs?|tablets?|caps?|capsules?|days?)$", "", s).strip()
    if field == FieldName.npi:
        s = re.sub(r"\D", "", s)
    if field == FieldName.strength:
        s = re.sub(r"(\d)\s*(mg|mcg|g|ml|units?)\b", r"\1 \2", s)
    if field in (FieldName.patient_name, FieldName.prescriber_name):
        s = re.sub(r"^dr\.?\s+", "", s)
        s = re.sub(r"[.,]", "", s)
    if field in (FieldName.dob, FieldName.date_written):
        s = re.sub(r"[.\-]", "/", s)
        s = re.sub(r"\b0(\d)/", r"\1/", s)        # 03/14 == 3/14
    return s.strip(" .;:") or None


def _agree(field: FieldName, a: str, b: str) -> bool:
    if field in EXACT:
        return a == b
    return fuzz.ratio(a, b) >= config.AGREE_RATIO * 100


def _decimal_variant(a: str, b: str) -> bool:
    """True if a and b differ only by a decimal point / zeros: '0.5 mg' vs '0.05 mg', '5' vs '50'."""
    if a == b:
        return False
    strip = lambda s: re.sub(r"[0.]", "", s)
    return bool(re.search(r"\d", a)) and strip(a) == strip(b)


def score_field(f: ExtractedField, ocr_text: str = "") -> ExtractedField:
    """Sets f.value, f.status, f.confidence and f.reasons from f.readings + f.checks.
    A pharmacist-confirmed field is never re-colored."""
    if f.status == Status.confirmed:
        return f
    reasons: list[str] = [r for r in f.reasons if r.startswith("reader ")]   # keep reader-error notes
    ocr = next((r for r in f.readings if r.source == "ocr"), None)
    llm = [r for r in f.readings if r.source != "ocr"]
    llm_read = [r for r in llm if r.value is not None]
    ocr_n = normalize(f.name, ocr.value) if ocr and ocr.value else None

    status = Status.review
    value, conf = None, 0.0

    if len(llm_read) == 2 and not _agree(f.name, normalize(f.name, llm_read[0].value), normalize(f.name, llm_read[1].value)):
        status = Status.conflict
        value = None
        reasons.append("readers disagree: " + "; ".join(f"{_who(r.source)} read {r.value!r}" for r in llm_read)
                       + (f"; OCR read {ocr.value!r}" if ocr and ocr.value else ""))
    elif len(llm_read) == 2:
        a, b = llm_read
        value, key = a.value, normalize(f.name, a.value)
        conf = (a.confidence + b.confidence) / 2
        if min(a.confidence, b.confidence) < config.OCR_GREEN:
            reasons.append("both readers agree but at least one marked it hard to read")
        elif ocr_n and _decimal_variant(key, ocr_n):
            status = Status.conflict
            reasons.append(f"OCR read {ocr.value!r} but both models read {value!r}: possible decimal/zero "
                           f"difference; check the fax image")
        elif not _grounded(f.name, key, ocr_n, ocr_text):
            reasons.append(f"both models read {value!r} but OCR could not confirm it on the page (model-only)")
        else:
            status = Status.verified
    elif len(llm_read) == 1:
        a = llm_read[0]
        value, conf = a.value, a.confidence * 0.5
        other = next((r for r in llm if r.value is None), None)
        if ocr_n and _agree(f.name, normalize(f.name, a.value), ocr_n) and ocr.confidence >= config.OCR_GREEN \
                and a.confidence >= config.OCR_GREEN:
            status, conf = Status.verified, (a.confidence + ocr.confidence) / 2
        else:
            reasons.append(f"only {_who(a.source)} could read this"
                           + (f"; {_who(other.source)} could not" if other else "; second reader unavailable"))
    else:
        if ocr and ocr.value:
            value, conf = ocr.value, (ocr.confidence or 0) * 0.5
            reasons.append(f"only OCR read this ({ocr.value!r}); neither vision reader could")
        else:
            reasons.append("no reader could read this field")

    if value and "?" in value:                    # a reader transcribed only part of it
        if f.name == FieldName.drug:
            status = Status.conflict
            reasons.append(f"drug name only partly legible ({value!r}); never dispense from a partial name")
        elif status == Status.verified:
            status = Status.review
            reasons.append(f"partly illegible: {value!r}")

    failed = [c for c in f.checks if not c.passed]
    if failed:
        status = Status.conflict
        reasons += [c.detail for c in failed]
    if f.candidates and status != Status.conflict:
        status = Status.conflict
        reasons.append("look-alike candidates: " + ", ".join(f.candidates) + " (pharmacist must choose)")

    f.value, f.status, f.confidence = value, status, round(conf, 3)
    f.reasons = list(dict.fromkeys(reasons)) if status != Status.verified else []
    if status != Status.verified and not f.reasons:
        f.reasons = ["needs review"]   # invariant: every amber/red field has a reason
    return f


def _grounded(field: FieldName, key: str, ocr_n: str | None, ocr_text: str) -> bool:
    """Pixel grounding: does the OCR evidence support the models' value?
    If OCR read this field, it must roughly match. If OCR found nothing for the field,
    fall back to a fuzzy search of the whole page text."""
    if field == FieldName.daw:
        return True        # checkbox: no text to ground; two readers + OCR pixel vote suffice
    if ocr_n:
        return fuzz.ratio(key, ocr_n) >= GROUND_RATIO or fuzz.partial_ratio(key, ocr_n) >= 90
    return bool(ocr_text) and fuzz.partial_ratio(key, ocr_text.lower()) >= GROUND_RATIO


def _who(source: str) -> str:
    label = source.split(":")[0]
    return {"ocr": "OCR", "reader_a": "reader A", "reader_b": "reader B"}.get(label, label)


def score_order(order: Order, ocr_text: str = "") -> Order:
    for f in order.fields.values():
        score_field(f, ocr_text)
    if order.status == OrderStatus.rejected:
        return order
    ok = all(order.fields.get(c) is not None and order.fields[c].status in (Status.verified, Status.confirmed)
             for c in CRITICAL_FIELDS)
    order.status = OrderStatus.ready_to_fill if ok and not order.guardrail_flags else OrderStatus.needs_pharmacist
    return order
