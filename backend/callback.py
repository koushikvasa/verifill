"""
Lane: Backend
Callback draft to the prescriber: deterministic text, no LLM. One line per problem field
stating what was written and what's wrong, addressed to the prescriber with the phone/fax
we have on file for the NPI (registry, never the number printed on the fax: a forged fax
would carry a forged number).

Only red (conflict) fields and failed checks go in the draft. Amber fields are a
pharmacist-review matter, not a prescriber call.
"""
from backend.models import FieldName, Order, OrderStatus, Status
from backend.verify import npi as npi_mod

LABEL = {FieldName.patient_name: "patient name", FieldName.dob: "patient DOB", FieldName.date_written: "date written",
         FieldName.drug: "drug", FieldName.strength: "strength", FieldName.dosage_form: "dosage form",
         FieldName.sig: "directions", FieldName.quantity: "quantity", FieldName.days_supply: "days supply",
         FieldName.refills: "refills", FieldName.daw: "DAW", FieldName.prescriber_name: "prescriber name",
         FieldName.npi: "NPI"}


def _issues(order: Order) -> list[str]:
    lines = []
    for f, ef in order.fields.items():
        if ef.status != Status.conflict:
            continue
        written = ef.value
        if written is None:
            seen = [r.value for r in ef.readings if r.value and not r.source.startswith("ocr")]
            written = " / ".join(dict.fromkeys(seen)) if seen else "(unreadable)"
        problems = [c.detail for c in ef.checks if not c.passed] or \
                   [r for r in ef.reasons if not r.startswith(("evidence only", "suggestion", "unverified"))]
        what = problems[0] if problems else "could not be verified"
        line = f"- {LABEL[f]}: written as \"{written}\". {what}."
        if ef.candidates and f == FieldName.drug:
            line += f" Please confirm which: {', '.join(ef.candidates)}."
        lines.append(line)
    return lines


def _best(order: Order, f: FieldName) -> str | None:
    """Agreed value, else the first vision reading (for addressing the letter only)."""
    ef = order.fields[f]
    return ef.value or next((r.value for r in ef.readings if r.value and not r.source.startswith("ocr")), None)


def draft(order: Order) -> str | None:
    """Fills order.callback_draft if the order needs a prescriber call. Returns the text."""
    if order.status in (OrderStatus.rejected, OrderStatus.not_prescription, OrderStatus.denied_by_prescriber):
        return order.callback_draft
    issues = _issues(order)
    if not issues:
        return None
    npi_val = order.fields[FieldName.npi].value or ""
    written_name = _best(order, FieldName.prescriber_name)
    rec = npi_mod.registry_record(npi_val) if npi_val else None
    if rec is None and written_name:                   # NPI unreadable: look the prescriber up by name
        found = npi_mod.search_by_name(written_name)
        rec = found[0] if len(found) == 1 else None
    name = (rec or {}).get("name") or written_name or "Prescriber"
    contact = (f" (phone {rec['phone']}, fax {rec['fax']})" if rec and rec.get("phone") else
               " (no registry contact on file; do not use the number printed on the fax without verifying it)")
    patient = _best(order, FieldName.patient_name) or "(patient name unreadable)"
    dob = _best(order, FieldName.dob) or "DOB unreadable"
    drug = _best(order, FieldName.drug) or "the prescribed medication"
    text = (f"To: {name}{contact}\n"
            f"Re: prescription for {patient} ({dob}), {drug}, received by fax {order.fax_file}\n\n"
            f"Before we can fill this prescription we need you to confirm the following:\n"
            + "\n".join(issues)
            + "\n\nPlease call or fax back a corrected prescription. Nothing has been dispensed.\n"
            f"Verifill Pharmacy, (212) 555-0100, fax (212) 555-0100. Order {order.id}.")
    order.callback_draft = text
    return text
