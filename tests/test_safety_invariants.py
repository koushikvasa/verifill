"""
Safety invariants (layer L8). Fast tests run offline on synthetic orders.
The end-to-end sample run calls the real readers; opt in with:
    $env:RUN_LIVE="1"; python -m pytest tests/test_safety_invariants.py -k live
"""
import json
import os

import pytest

from backend import config, safety, scoring
from backend.models import (CRITICAL_FIELDS, Check, ExtractedField, FieldName as F, Order, OrderStatus, PickStatus,
                            PickTicket, Reading, Status)
from backend.verify import lasa, npi, rules


def _field(name: F, a: str | None, b: str | None, ocr: str | None = None, conf=0.9) -> ExtractedField:
    rs = [Reading(source="reader_a:m1", value=a, confidence=conf), Reading(source="reader_b:m2", value=b, confidence=conf)]
    if ocr is not None:
        rs.append(Reading(source="ocr", value=ocr, confidence=0.9))
    return ExtractedField(name=name, readings=rs)


def _green_order() -> Order:
    o = Order(id="T-1", fax_file="t.png")
    for f in F:
        v = {F.strength: "10 mg", F.npi: "1234567893"}.get(f, "x")
        o.fields[f] = _field(f, v, v, ocr=v)
    scoring.score_order(o)
    return o


# ---------- scoring: never auto-correct, disagreement is red ----------

def test_readers_disagree_is_red_with_both_values():
    f = scoring.score_field(_field(F.strength, "0.5 mg", "0.05 mg"))
    assert f.status == Status.conflict and f.value is None
    assert any("0.5 mg" in r and "0.05 mg" in r for r in f.reasons)


def test_model_silent_decimal_correction_is_caught_against_ocr():
    f = scoring.score_field(_field(F.strength, "0.05 mg", "0.05 mg", ocr="0.5 mg"))
    assert f.status == Status.conflict


def test_agreed_value_is_kept_verbatim():
    f = scoring.score_field(_field(F.strength, "0.5 mg", "0.5 mg", ocr="0.5 mg"))
    assert f.value == "0.5 mg"          # transcribed, not corrected


def test_partial_drug_name_is_red_even_if_readers_agree():
    f = scoring.score_field(_field(F.drug, "Hy?????zine", "Hy?????zine", conf=0.3))
    assert f.status == Status.conflict


def test_single_reader_is_never_green():
    f = scoring.score_field(_field(F.npi, "1234567893", None))
    assert f.status == Status.review and f.reasons


def test_every_non_green_field_has_a_reason():
    for a, b in [(None, None), ("1", "2"), ("x", None)]:
        f = scoring.score_field(_field(F.refills, a, b))
        assert f.status == Status.verified or f.reasons


def test_failed_check_turns_field_red():
    ef = _field(F.quantity, "#90", "#90", ocr="#90")
    ef.checks.append(Check(name="qty_days", passed=False, detail="mismatch"))
    assert scoring.score_field(ef).status == Status.conflict


def test_ready_to_fill_requires_all_critical_fields():
    o = _green_order()
    assert o.status == OrderStatus.ready_to_fill
    o.fields[F.npi] = _field(F.npi, "1", "2")
    scoring.score_order(o)
    assert o.status == OrderStatus.needs_pharmacist


def test_guardrail_flag_blocks_ready_to_fill():
    o = _green_order()
    o.guardrail_flags = ["possible prompt injection"]
    scoring.score_order(o)
    assert o.status == OrderStatus.needs_pharmacist


# ---------- safety gate: repairs anything upstream got wrong ----------

def test_gate_downgrades_verified_field_with_one_source():
    o = _green_order()
    o.fields[F.drug].readings = o.fields[F.drug].readings[:1]      # simulate a scoring bug
    found = safety.gate(o)
    assert found and o.status == OrderStatus.needs_pharmacist
    assert o.fields[F.drug].status == Status.review


def test_gate_blocks_pick_ticket_when_drug_not_green():
    o = _green_order()
    o.fields[F.drug].status = Status.conflict
    o.fields[F.drug].reasons = ["look-alike"]
    o.pick_ticket = PickTicket(status=PickStatus.in_stock)
    safety.gate(o)
    assert o.pick_ticket.status == PickStatus.blocked


def test_gate_rejects_confirmed_without_pharmacist_audit():
    o = _green_order()
    o.fields[F.drug].status = Status.confirmed
    assert any("confirmed without a pharmacist" in v for v in safety.violations(o))


def test_gate_passes_clean_order():
    o = _green_order()
    assert safety.gate(o) == [] and o.status == OrderStatus.ready_to_fill


# ---------- verification ----------

def test_decimal_error_levothyroxine():
    c = rules.decimal_error("levothyroxine", "0.5 mg")
    assert c and not c.passed and "0.05" in c.detail


def test_max_daily_dose_exceeded():
    assert not rules.max_daily_dose("levothyroxine", "0.5 mg", "1 tab PO daily").passed


def test_qty_days_mismatch():
    assert not rules.qty_days("#90 (ninety)", "30", "1 tab PO once daily").passed


def test_npi_transposition_suggestion():
    checks, sugg, _ = npi.checks_for("1987654382", "Daniel Reyes, DO")
    assert not checks[0].passed and "1987654328" in sugg


def test_npi_name_mismatch():
    ok, _ = npi.names_match("Dr. Maya Okafor", "James Whitfield, MD")
    assert not ok
    assert npi.names_match("Dr. Maya Okafor", "Okafor, Maya")[0]


def test_lasa_partial_name_gives_both_candidates_and_evidence_only():
    c, cands, ev = lasa.check(["Hy????yzine"], clean=False, sig="1 tab q6h PRN itching")
    assert c and not c.passed and set(cands) == {"hydroxyzine", "hydralazine"}
    assert ev and all(e.startswith("evidence only: ") for e in ev)


def test_lasa_clean_read_of_pair_member_passes():
    c, cands, _ = lasa.check(["Lisinopril"], clean=True, sig="1 tab daily")
    assert c.passed and not cands


# ---------- end to end (live readers, opt-in) ----------

@pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="calls vision models; set RUN_LIVE=1")
def test_live_only_rx001_ready_and_no_false_ready():
    from backend import pipeline
    gt = json.loads((config.SAMPLES_DIR / "ground_truth.json").read_text())
    cases = gt if isinstance(gt, list) else list(gt.values())
    false_ready = []
    for c in cases:
        o = pipeline.run(config.SAMPLES_DIR / c["file"])
        assert safety.violations(o) == [], c["id"]
        if o.status == OrderStatus.ready_to_fill and c["expected_outcome"] != "ready_to_fill":
            false_ready.append(c["id"])
        if c["expected_outcome"] == "rejected":
            assert o.status == OrderStatus.rejected, c["id"]
        for ef in o.fields.values():
            assert ef.status in (Status.verified, Status.confirmed) or ef.reasons, (c["id"], ef.name)
        if o.status == OrderStatus.ready_to_fill:
            assert all(o.fields[f].status == Status.verified for f in CRITICAL_FIELDS)
    assert false_ready == []
