"""
Lane: Backend
Orchestrator. One fax in -> one Order out. Each stage is traced in PRISM
with its own agent_name (verifill-<stage>), session_id = order id.

fax -> OCR -> guard -> readers A+B -> (zoom: dropped) -> verify (KB/RxNav/NPPES + LASA + rules)
    -> score -> inventory (only if drug/strength/form green) -> callback draft -> Order

Fail-closed (safety layer L5): an exception in any stage is recorded in the audit trail
and can only push the order toward needs_pharmacist, never toward ready_to_fill.
"""
import uuid
from pathlib import Path

from PIL import Image

from backend import guard, scoring, tracing
from backend.extract import ocr, vision
from backend.models import (AuditEvent, ExtractedField, FieldName, Order, OrderStatus, PickStatus,
                            PickTicket, Status)

PICK_FIELDS = (FieldName.drug, FieldName.strength, FieldName.dosage_form)


def run(fax_path: Path) -> Order:
    order = Order(id=f"ORD-{uuid.uuid4().hex[:8]}", fax_file=fax_path.name)
    img = Image.open(fax_path)
    img.load()                                     # decode once; readers run in threads
    order.audit.append(AuditEvent(actor="pipeline", action="received", detail=fax_path.name))
    failures: list[str] = []

    # 1. OCR (non-LLM) -> raw text for the guardrail BEFORE any model sees the fax
    ocr_text, ocr_fields = "", {f: None for f in FieldName}
    try:
        with tracing.step("ocr", order.id, model="tesseract", input_obj=fax_path.name) as t:
            ocr_text, ocr_fields, _ = ocr.read(img)
            t["output"] = {f.value: (r.value if r else None) for f, r in ocr_fields.items()}
    except Exception as e:  # noqa: BLE001  fail closed
        failures.append(f"ocr failed: {e}")
        _audit(order, "ocr", "error", str(e))

    order.guardrail_flags = guard.check(ocr_text, order.id)
    if order.guardrail_flags:
        return _reject(order, "guard", order.guardrail_flags)

    # 2. readers A + B (different vendors), concurrently
    try:
        readers = vision.read_both(img, session_id=order.id)
    except Exception as e:  # noqa: BLE001  read_both shouldn't raise; belt and braces
        readers = []
        failures.append(f"vision readers failed: {e}")
    for r in readers:
        _audit(order, r.label, "read", f"{r.model}: " + (r.error or f"ok ({r.attempts} attempt)"))
    out_flags = [fl for r in readers for fl in r.flags]
    if out_flags:                                  # a reader echoed injected instructions
        order.guardrail_flags = out_flags
        return _reject(order, "guard", out_flags)

    for f in FieldName:
        ef = ExtractedField(name=f)
        if ocr_fields.get(f):
            ef.readings.append(ocr_fields[f])
        for r in readers:
            if r.fields.get(f):
                ef.readings.append(r.fields[f])
            elif r.error:
                ef.reasons.append(f"reader {r.label[-1].upper()} unavailable: {r.error}")
        order.fields[f] = ef

    # 4. verify: backend/verify (A5). Missing module or failure -> fail closed.
    try:
        from backend import verify
        if hasattr(verify, "run_all"):
            with tracing.step("verify", order.id, input_obj=_values(order)) as t:
                verify.run_all(order, ocr_text)
                t["output"] = {f.value: [c.model_dump() for c in ef.checks] for f, ef in order.fields.items() if ef.checks}
    except Exception as e:  # noqa: BLE001
        failures.append(f"verification failed: {e}")
        _audit(order, "verify", "error", str(e))

    # 5. score
    with tracing.step("score", order.id, input_obj=_values(order)) as t:
        scoring.score_order(order, ocr_text)
        t["output"] = {"status": order.status.value,
                       "fields": {f.value: {"value": ef.value, "status": ef.status.value, "reasons": ef.reasons}
                                  for f, ef in order.fields.items()}}

    if failures:                                   # L5: any stage failure blocks auto-fill
        order.status = OrderStatus.needs_pharmacist
        for fl in failures:
            _audit(order, "pipeline", "fail_closed", fl)

    # 6. inventory: only when drug/strength/form are green (defence in depth; inventory also checks)
    order.pick_ticket = pick_ticket(order)

    _audit(order, "pipeline", "done", order.status.value)
    tracing.submit_order(order.id, "success" if order.status == OrderStatus.ready_to_fill else "failure",
                         summary=_summary(order))
    return order


def pick_ticket(order: Order) -> PickTicket:
    not_green = [f.value for f in PICK_FIELDS
                 if order.fields.get(f) is None or order.fields[f].status not in (Status.verified, Status.confirmed)]
    if not_green:
        return PickTicket(status=PickStatus.blocked,
                          warnings=[f"blocked until verified by pharmacist: {', '.join(not_green)}"])
    try:
        from backend import inventory
        if hasattr(inventory, "build_pick_ticket"):
            return inventory.build_pick_ticket(order)
        return PickTicket(status=PickStatus.blocked, warnings=["inventory lookup not available yet"])
    except Exception as e:  # noqa: BLE001
        _audit(order, "inventory", "error", str(e))
        return PickTicket(status=PickStatus.blocked, warnings=[f"inventory error: {e}"])


def _reject(order: Order, actor: str, flags: list[str]) -> Order:
    order.status = OrderStatus.rejected
    _audit(order, actor, "flagged", "; ".join(flags))
    order.pick_ticket = PickTicket(status=PickStatus.blocked, warnings=["order rejected by guardrail"])
    tracing.submit_order(order.id, "failure", summary="rejected: " + "; ".join(flags))
    return order


def _audit(order: Order, actor: str, action: str, detail: str = "") -> None:
    order.audit.append(AuditEvent(actor=actor, action=action, detail=detail[:500]))


def _values(order: Order) -> dict:
    return {f.value: [(r.source, r.value) for r in ef.readings] for f, ef in order.fields.items()}


def _summary(order: Order) -> str:
    flagged = {f.value: ef.status.value for f, ef in order.fields.items() if ef.status != Status.verified}
    return f"status={order.status.value}; non-green fields={flagged}"
