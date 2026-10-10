"""A9 DEA check and A8 zoom bookkeeping (offline)."""
from backend import scoring
from backend.models import ExtractedField, FieldName as F, Reading, Status
from backend.verify import dea


def test_dea_check_digit_and_name_letter():
    # AB1234563: (1+3+5) + 2*(2+4+6) = 33 -> check digit 3
    assert dea.check_digit_ok("AB1234563")
    assert not dea.check_digit_ok("AB1234567")
    assert dea.validate("AB1234563", "Dr. Anna Brown").passed
    assert "check digit" in dea.validate("AB1234567", "Anna Brown").detail
    c = dea.validate("AB1234563", "Maya Okafor, MD")
    assert not c.passed and "second letter" in c.detail
    assert dea.validate("M91234563", "Jane Doe, NP").passed        # mid-level, supervisor's number
    assert dea.find("Lic NY 284113 DEA: BO1234563 NPI 1234567893") == ["BO1234563"]
    assert dea.find("no number here 1234567") == []
    assert not dea.missing().passed


def _f(readings):
    return ExtractedField(name=F.sig, readings=readings)


def test_zoom_reading_replaces_only_a_worse_page_reading():
    a = Reading(source="reader_a:m", value="1 tab PO ??? x 10 days", confidence=0.3)
    b = Reading(source="reader_b:m", value="1 tab PO TID x 10 days", confidence=0.9)
    za = Reading(source="zoom:reader_a:m", value="1 tab PO TID x 10 days", confidence=0.85)
    zb = Reading(source="zoom:reader_b:m", value="1 tab PO BID x 10 days", confidence=0.85)   # must NOT replace b
    votes = scoring._per_reader([a, b, za, zb])
    assert [v.source for v in votes] == ["zoom:reader_a:m", "reader_b:m"]
    ef = scoring.score_field(_f([a, b, za, zb, Reading(source="ocr", value="1 tab PO TID x 10 days", confidence=0.8)]))
    assert ef.status == Status.verified and ef.value == "1 tab PO TID x 10 days"


def test_zoom_cannot_go_green_without_grounding():
    a = Reading(source="reader_a:m", value=None, confidence=0.3)
    b = Reading(source="reader_b:m", value=None, confidence=0.3)
    za = Reading(source="zoom:reader_a:m", value="Robert Kim", confidence=0.85)
    zb = Reading(source="zoom:reader_b:m", value="Robert Kim", confidence=0.85)
    ef = scoring.score_field(ExtractedField(name=F.patient_name, readings=[a, b, za, zb]), "")
    assert ef.status == Status.review and any("OCR could not confirm" in r for r in ef.reasons)
