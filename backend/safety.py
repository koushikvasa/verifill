"""
Lane: Backend
Final safety gate (safety layer L6). Runs last, after scoring and inventory, and
re-asserts the CLAUDE.md invariants against the finished Order. It does not trust the
code that ran before it: any violation downgrades the order and is logged, so a bug in
scoring/verify can never auto-fill a prescription.

Invariants:
1. ready_to_fill  => every CRITICAL_FIELD is verified/confirmed and there are no guardrail flags
2. verified field => >= 2 independent sources agree on its value, no failed check,
                     no partial ("?") value, no "unverified:" note
3. confirmed field=> a pharmacist audit event exists for it
4. amber/red field=> at least one human-readable reason
5. pick ticket not blocked => drug, strength, dosage_form are verified/confirmed
"""
from backend import scoring, tracing
from backend.models import (CRITICAL_FIELDS, AuditEvent, FieldName, Order, OrderStatus, PickStatus,
                            PickTicket, Status)

GREEN = (Status.verified, Status.confirmed)
PICK_FIELDS = (FieldName.drug, FieldName.strength, FieldName.dosage_form)


def violations(order: Order) -> list[str]:
    v: list[str] = []
    for f, ef in order.fields.items():
        if ef.status == Status.verified:
            keys = [scoring.normalize(f, r.value) for r in ef.readings if r.value is not None]
            target = scoring.normalize(f, ef.value)
            agreeing = sum(1 for k in keys if k is not None and target is not None and scoring._agree(f, k, target))
            if agreeing < 2:
                v.append(f"{f.value} is verified with only {agreeing} agreeing source(s)")
            if any(not c.passed for c in ef.checks):
                v.append(f"{f.value} is verified despite a failed check")
            if ef.value and "?" in ef.value:
                v.append(f"{f.value} is verified with a partial value {ef.value!r}")
            if any(r.startswith("unverified: ") for r in ef.reasons):
                v.append(f"{f.value} is verified despite an unverified note")
        if ef.status == Status.confirmed and not any(
                a.actor.startswith("pharmacist") and f.value in a.detail for a in order.audit):
            v.append(f"{f.value} is confirmed without a pharmacist audit event")
        if ef.status in (Status.review, Status.conflict) and not ef.reasons:
            v.append(f"{f.value} is {ef.status.value} without a reason")

    if order.status == OrderStatus.ready_to_fill:
        missing = [c.value for c in CRITICAL_FIELDS
                   if order.fields.get(c) is None or order.fields[c].status not in GREEN]
        if missing:
            v.append(f"ready_to_fill with non-green critical fields: {', '.join(sorted(missing))}")
        if order.guardrail_flags:
            v.append("ready_to_fill despite guardrail flags")

    pt = order.pick_ticket
    if pt and pt.status != PickStatus.blocked:
        not_green = [f.value for f in PICK_FIELDS if order.fields.get(f) is None or order.fields[f].status not in GREEN]
        if not_green:
            v.append(f"pick ticket unlocked while {', '.join(not_green)} not verified")
        if order.status == OrderStatus.rejected:
            v.append("pick ticket unlocked on a rejected order")
    return v


def gate(order: Order) -> list[str]:
    """Check invariants, repair toward safety, log. Returns the violations found."""
    with tracing.step("safety_gate", order.id, input_obj={"status": order.status.value}) as t:
        found = violations(order)
        if found:
            for ef in order.fields.values():
                if ef.status in (Status.review, Status.conflict) and not ef.reasons:
                    ef.reasons.append("needs review (added by safety gate)")
                if ef.status == Status.verified and any(found_for(ef.name.value, x) for x in found):
                    ef.status = Status.review
                    ef.reasons.append("downgraded by safety gate: " + "; ".join(x for x in found
                                                                                 if found_for(ef.name.value, x)))
            if order.status == OrderStatus.ready_to_fill:
                order.status = OrderStatus.needs_pharmacist
            if order.pick_ticket and order.pick_ticket.status != PickStatus.blocked:
                order.pick_ticket = PickTicket(status=PickStatus.blocked,
                                               warnings=["blocked by safety gate: " + "; ".join(found)])
            order.audit.append(AuditEvent(actor="safety_gate", action="violation", detail="; ".join(found)[:500],
                                          trace_id=None))
        else:
            order.audit.append(AuditEvent(actor="safety_gate", action="passed", detail="all invariants hold"))
        t["output"] = {"violations": found, "status": order.status.value}
    return found


def found_for(field: str, violation: str) -> bool:
    return violation.startswith(field + " ")
