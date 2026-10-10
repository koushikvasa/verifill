"""
Lane: Backend
run_all(order, ocr_text): runs every grounded check and attaches it to the right field.
A failed check turns the field red (scoring.py). Notes starting with "unverified: " cap a
field at amber; "evidence only: " / "suggestion: " notes are shown to the pharmacist and
never decide anything.
"""
from backend import scoring
from backend.models import Check, ExtractedField, FieldName as F, Order, Status
from backend.verify import lasa, npi, rules, rxnorm


def _prelim(order: Order, ocr_text: str) -> dict[F, ExtractedField]:
    """Score a copy of each field from readings alone, to know which values are trustworthy inputs."""
    return {f: scoring.score_field(ef.model_copy(deep=True, update={"checks": [], "candidates": []}), ocr_text)
            for f, ef in order.fields.items()}


def _llm_values(ef: ExtractedField) -> list[str]:
    return list(dict.fromkeys(r.value for r in ef.readings if r.source != "ocr" and r.value))


def reset(order: Order) -> None:
    """Clear checks/candidates/verify notes so run_all can be re-run (after a pharmacist edit)."""
    for ef in order.fields.values():
        ef.checks, ef.candidates = [], []
        ef.reasons = [r for r in ef.reasons if r.startswith(("reader ", "pharmacist override"))]
        if ef.status == Status.conflict and any(r.source.startswith("pharmacist:") for r in ef.readings):
            ef.status = Status.confirmed          # re-opened confirmation: re-check it from scratch


def run_all(order: Order, ocr_text: str = "") -> None:
    fx = order.fields
    pre = _prelim(order, ocr_text)
    val = {f: (pre[f].value if pre[f].status != Status.conflict else None) for f in F}

    def add(f: F, c: Check | None):
        if c is not None:
            fx[f].checks.append(c)

    def note(f: F, text: str):
        if text not in fx[f].reasons:
            fx[f].reasons.append(text)

    # --- drug identity + look-alikes ---
    if fx[F.drug].status == Status.confirmed:             # pharmacist decided: their value is the reading
        drug_readings = [fx[F.drug].value] if fx[F.drug].value else []
    else:
        drug_readings = _llm_values(fx[F.drug]) or ([pre[F.drug].value] if pre[F.drug].value else [])
    clean = pre[F.drug].status in (Status.verified, Status.confirmed)
    c, cands, evidence = lasa.check(drug_readings, clean, val[F.sig])
    add(F.drug, c)
    fx[F.drug].candidates = cands
    for e in evidence:
        note(F.drug, e)
    drug = val[F.drug] if rxnorm.canonical(val[F.drug]) else None
    if drug is None and drug_readings and not cands:
        # readers disagree only between a partial and a full read of the SAME drug
        # ("Lev?thyro?ne" vs "Levothyroxine"): still run the dose checks against it
        resolved = {m for r in drug_readings for m in lasa.matches(r)}
        if len(resolved) == 1 and all(lasa.matches(r) for r in drug_readings):
            drug = resolved.pop()
            note(F.drug, f"evidence only: every reading is consistent with {drug}")
    if val[F.drug] and "?" not in val[F.drug] and not cands:
        add(F.drug, rxnorm.drug_exists(val[F.drug]))
        add(F.drug, rules.controlled_schedule(val[F.drug]))

    # --- strength / form / dose: only meaningful once the drug is identified ---
    if drug:
        if val[F.strength]:
            add(F.strength, rxnorm.strength_marketed(drug, val[F.strength]))
            add(F.strength, rules.decimal_error(drug, val[F.strength]))
            if val[F.sig]:
                add(F.strength, rules.max_daily_dose(drug, val[F.strength], val[F.sig]))
            ev = rxnorm.rxnav_evidence(rxnorm.canonical(drug), val[F.strength])
            if ev:
                note(F.strength, f"evidence only: {ev}")
        if val[F.dosage_form]:
            add(F.dosage_form, rxnorm.form_marketed(drug, val[F.dosage_form]))
    else:
        for f in (F.strength, F.dosage_form):
            note(f, "unverified: cannot check against the drug reference until the drug is identified")

    # --- quantity / days / dates / refills ---
    if val[F.quantity]:
        add(F.quantity, rules.qty_words(val[F.quantity]))
        if val[F.days_supply] and val[F.sig]:
            c = rules.qty_days(val[F.quantity], val[F.days_supply], val[F.sig])
            add(F.quantity, c)
            if c is not None and not c.passed:
                add(F.days_supply, c)
    if val[F.date_written]:
        add(F.date_written, rules.date_written(val[F.date_written]))
    if val[F.dob]:
        add(F.dob, rules.dob(val[F.dob]))
    if val[F.refills]:
        add(F.refills, rules.refills(val[F.refills]))

    # --- prescriber ---
    _prescriber(order, pre, val, add, note)


def _prescriber(order, pre, val, add, note) -> None:
    fx = order.fields
    written = val[F.prescriber_name]
    npi_vals = [v for v in _llm_values(fx[F.npi]) if "?" not in v]

    if val[F.npi]:                                           # one agreed NPI value
        checks, sugg, notes = npi.checks_for(val[F.npi], written)
        for c in checks:
            add(F.npi, c)
            if c.name == "prescriber_name_match" and not c.passed:
                add(F.prescriber_name, c)
        if sugg:
            fx[F.npi].candidates = sugg
            note(F.npi, "suggestion: " + ", ".join(sugg) + " (verify with prescriber before using)")
        for n in notes:
            note(F.npi, n)
    elif len(npi_vals) > 1:                                  # readers disagree: evidence per reading
        valid = []
        for v in npi_vals:
            checks, _, _ = npi.checks_for(v, written)
            bad = [c.detail for c in checks if not c.passed]
            if not bad:
                valid.append(v)
            note(F.npi, f"evidence only: reading {v}: " + ("; ".join(bad) if bad else
                                                          "valid, registered, name matches"))
        fx[F.npi].candidates = valid
    elif not npi_vals:                                       # NPI missing / unreadable
        if written:
            found = npi.search_by_name(written)
            if found:
                fx[F.npi].candidates = [p["npi"] for p in found if p.get("npi")][:10]
                note(F.npi, f"suggestion: NPI not readable on fax; registry has {len(found)} match(es) for "
                            f"{written}: {', '.join(fx[F.npi].candidates[:5])}. Pharmacist must confirm.")
            else:
                add(F.npi, Check(name="npi_registry", passed=False, grounded_in="local",
                                 detail=f"NPI not readable and no registered prescriber named {written}; callback needed"))
        else:
            note(F.npi, "unverified: neither NPI nor prescriber name is readable; callback needed")
