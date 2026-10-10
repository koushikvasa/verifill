"""
Lane: Backend
Orchestrator. One fax in -> one or more Orders out. Each stage is traced in PRISM with its
own agent_name (verifill-<stage>); page-level stages use session_id = fax id, order-level
stages use session_id = order id.

file -> ingest (pages, orientation, dewarp) -> OCR every page -> guard EVERY page (before any
model call) -> readers A+B per page -> classify (keywords + both readers) ->
  cover_sheet: skip | other: not_prescription | refill_authorization: approved/denied |
  new_rx: one Order per medication (shared fax_id)
-> per order: verify -> score -> inventory (only if drug/strength/form green) -> safety gate

Fail-closed (safety layer L5): an exception in any stage is recorded in the audit trail
and can only push an order toward needs_pharmacist, never toward ready_to_fill.
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image
from rapidfuzz import fuzz

from backend import callback, config, guard, ingest, safety, scoring, tracing
from backend.extract import ground_ocr, ocr, vision
from backend.models import (AuditEvent, ExtractedField, FaxType, FieldName, Order, OrderStatus, PickStatus,
                            PickTicket, Reading, Status)

PICK_FIELDS = (FieldName.drug, FieldName.strength, FieldName.dosage_form)
OCR_TEXT: dict[str, str] = {}      # order id -> page OCR text, for re-scoring after a pharmacist edit
PAGE_IMAGES: dict[str, Image.Image] = {}   # order id -> cleaned page the readers saw (for the UI boxes)
DROPPABLE = {FaxType.cover_sheet, FaxType.other}


def run(fax_path: Path) -> Order:
    """Backwards-compatible: the first order of the fax."""
    return run_fax(fax_path)[0]


def run_fax(fax_path: Path) -> list[Order]:
    fax_id = f"FAX-{uuid.uuid4().hex[:8]}"
    notes: list[str] = []
    try:
        with tracing.step("ingest", fax_id, input_obj=fax_path.name) as t:
            pages = ingest.prepare(fax_path)
            t["output"] = [{"page": p.number, "rotated": p.rotated, "dewarped": p.dewarped} for p in pages]
        notes += [n for p in pages for n in p.notes]
    except Exception as e:  # noqa: BLE001  unreadable file: fail closed
        o = _new_order(fax_id, fax_path, 1)
        _audit(o, "ingest", "error", str(e))
        o.pick_ticket = PickTicket(status=PickStatus.blocked, warnings=["fax could not be opened"])
        return [o]

    # 1. OCR every page, then guard EVERY page before any model sees any of it
    page_ocr: list[tuple[str, dict, str | None]] = []
    for p in pages:
        try:
            with tracing.step("ocr", fax_id, model="tesseract", input_obj=f"page {p.number}") as t:
                text, fields, _ = ocr.read(p.image)
                t["output"] = {f.value: (r.value if r else None) for f, r in fields.items()}
            page_ocr.append((text, fields, None))
        except Exception as e:  # noqa: BLE001
            page_ocr.append(("", {f: None for f in FieldName}, f"ocr failed on page {p.number}: {e}"))
    flags = [f"page {p.number}: {fl}" for p, (text, _, _) in zip(pages, page_ocr) for fl in guard.check(text, fax_id)]
    if flags:
        return [_reject(_new_order(fax_id, fax_path, 1), "guard", flags, notes)]

    # 2. both readers on every page + the grounding OCR engine, concurrently
    with ThreadPoolExecutor(max_workers=6) as ex:
        ground_jobs = [ex.submit(ground_ocr.text, p.image) for p in pages]
        readers = list(ex.map(lambda p: vision.read_both(p.image, session_id=fax_id), pages))
        ground = [j.result() for j in ground_jobs]
    if any(ground):
        with tracing.step("ground_ocr", fax_id, model=config.GROUND_OCR, input_obj=f"{len(pages)} page(s)") as t:
            t["output"] = [g[:2000] for g in ground]
    out_flags = [f"page {p.number}: {fl}" for p, g in zip(pages, ground) for fl in guard.local_check(g)]
    out_flags += [f"page {p.number}: {fl}" for p, rs in zip(pages, readers) for r in rs for fl in r.flags]
    if out_flags:                                  # a reader echoed injected instructions
        return [_reject(_new_order(fax_id, fax_path, 1), "guard", out_flags, notes)]

    # 3. classify each page and build orders
    orders: list[Order] = []
    filed: list[str] = []
    for p, (text, ocr_fields, ocr_err), rs, g in zip(pages, page_ocr, readers, ground):
        kind, why = _page_type(text, rs)
        text = f"{text}\n{g}" if g else text      # grounding sees both OCR engines' text
        notes.append(f"page {p.number}: {kind.value} ({why})")
        if kind == FaxType.cover_sheet:
            notes.append(f"page {p.number} skipped: cover sheet")
            continue
        if kind == FaxType.other:
            filed.append(f"page {p.number}")
            continue
        orders += _orders_for_page(fax_id, fax_path, p, kind, text, ocr_fields, rs, ocr_err)

    if not orders:
        o = _new_order(fax_id, fax_path, 1)
        if filed:
            o.status, o.fax_type = OrderStatus.not_prescription, FaxType.other
            _audit(o, "classify", "filed", f"not a prescription ({', '.join(filed)}): filed, no order created")
        else:
            _audit(o, "classify", "no_prescription_found", "no prescription page found; pharmacist please review")
        o.pick_ticket = PickTicket(status=PickStatus.blocked, warnings=["no prescription on this fax"])
        orders = [o]
    for o in orders:
        o.audit[1:1] = [AuditEvent(actor="ingest", action="page", detail=n) for n in notes]
    tracing.submit_order(fax_id, "success" if all(o.status == OrderStatus.ready_to_fill for o in orders) else "failure",
                         summary="; ".join(f"{o.id}={o.status.value}" for o in orders))
    return orders


def _page_type(ocr_text: str, readers: list) -> tuple[FaxType, str]:
    """Keyword vote + both readers. A page is only skipped/filed if ALL of them agree;
    any disagreement -> treat as a prescription so the pharmacist sees it."""
    kw, _ = ingest.classify(ocr_text)
    votes = {FaxType(r.page_type) for r in readers if r.page_type}
    n_readers = sum(1 for r in readers if r.page_type)
    if kw in DROPPABLE and votes == {kw} and n_readers == 2:
        return kw, "keywords and both readers agree"
    if len(votes) == 1 and n_readers == 2 and not (votes & DROPPABLE):
        return next(iter(votes)), f"both readers agree (keywords said {kw.value})"
    if kw not in DROPPABLE and not votes - {kw}:
        return kw, "keywords" + (" and readers agree" if votes else "; readers unavailable")
    return FaxType.new_rx, (f"classifiers disagree (keywords {kw.value}, readers "
                            f"{sorted(v.value for v in votes)}): treated as a prescription")


def _match_meds(rs: list) -> list[list[tuple[str, dict]]]:
    """Pair medications across readers by drug name. -> [[(label, med_readings), ...], ...]"""
    a = [(rs[0].label, m) for m in rs[0].meds] if rs else []
    b = [(rs[1].label, m) for m in rs[1].meds] if len(rs) > 1 else []
    drug = lambda m: ((m.get(FieldName.drug) and m[FieldName.drug].value) or "").lower()
    groups, used = [], set()
    for la, ma in a:
        best, bi = 0, None
        for j, (_, mb) in enumerate(b):
            da, db = drug(ma), drug(mb)
            sc = fuzz.ratio(da, db) if da and db else (100 if not da and not db else 0)
            if j not in used and sc > best:
                best, bi = sc, j
        if bi is not None and best >= 60:
            used.add(bi)
            groups.append([(la, ma), b[bi]])
        else:
            groups.append([(la, ma)])
    groups += [[b[j]] for j in range(len(b)) if j not in used]
    return groups


def _orders_for_page(fax_id, fax_path, page, kind, ocr_text, ocr_fields, rs, ocr_err) -> list[Order]:
    groups = _match_meds(rs) or [[]]
    # label-parsed OCR fields only make sense on a single-drug Rx pad; elsewhere OCR grounds via full text
    use_ocr_fields = kind == FaxType.new_rx and len(groups) == 1 and any(
        ocr_fields.get(f) and ocr_fields[f].value for f in (FieldName.sig, FieldName.quantity))
    orders = []
    for gi, group in enumerate(groups):
        o = _new_order(fax_id, fax_path, page.number)
        o.fax_type, o.orientation_fixed = kind, page.rotated
        OCR_TEXT[o.id], PAGE_IMAGES[o.id] = ocr_text, page.image
        for r in rs:
            _audit(o, r.label, "read", f"{r.model}: " + (r.error or f"ok, {len(r.meds)} medication(s)"))
        found_by = {label for label, _ in group}
        for f in FieldName:
            ef = ExtractedField(name=f)
            if use_ocr_fields and ocr_fields.get(f):
                ef.readings.append(ocr_fields[f])
            for r in rs:
                src = r.shared if f in vision.SHARED else next((m for lbl, m in group if lbl == r.label), {})
                if src.get(f):
                    ef.readings.append(src[f])
                elif r.error:
                    ef.reasons.append(f"reader {r.label[-1].upper()} unavailable: {r.error}")
                elif f in vision.MED and group and r.label not in found_by:
                    ef.reasons.append(f"reader {r.label[-1].upper()} did not list this medication")
            o.fields[f] = ef
        if len(groups) > 1:
            _audit(o, "split", "medication", f"medication {gi + 1} of {len(groups)} on page {page.number}")
        _finish(o, ocr_text, [ocr_err] if ocr_err else [], refill=rs if kind == FaxType.refill_authorization else None)
        orders.append(o)
    return orders


def _refill(o: Order, rs: list) -> None:
    """Prescriber's answer on a refill request. Denied by both readers -> no fill."""
    answers = [r.refill_response for r in rs if not r.error]
    note = next((r.refill_note for r in rs if r.refill_note), None)
    if answers and all(a == "denied" for a in answers):
        o.status = OrderStatus.denied_by_prescriber
        o.callback_draft = (f"Refill request DENIED by the prescriber"
                            + (f" (reason: {note})" if note else "") + ". Notify the patient; do not fill.")
        _audit(o, "refill", "denied", f"prescriber denied the refill; reason: {note or 'none given'}")
    elif answers and all(a == "approved" for a in answers):
        _audit(o, "refill", "approved", "prescriber approved the refill (renewal order)")
    else:
        o.fields[FieldName.refills].reasons.append(
            f"unverified: prescriber's response unclear (readers saw {answers or 'nothing'})")
        _audit(o, "refill", "unclear", f"readers saw {answers}")


def _finish(order: Order, ocr_text: str, failures: list[str], refill: list | None = None) -> Order:
    """verify -> score -> (refill answer) -> inventory -> safety gate for one order."""
    try:
        from backend import verify
        with tracing.step("verify", order.id, input_obj=_values(order)) as t:
            verify.run_all(order, ocr_text)
            t["output"] = {f.value: [c.model_dump() for c in ef.checks] for f, ef in order.fields.items() if ef.checks}
    except Exception as e:  # noqa: BLE001
        failures.append(f"verification failed: {e}")
        _audit(order, "verify", "error", str(e))
    if refill is not None:
        _refill(order, refill)

    with tracing.step("score", order.id, input_obj=_values(order)) as t:
        if order.status != OrderStatus.denied_by_prescriber:
            scoring.score_order(order, ocr_text)
        else:
            for ef in order.fields.values():
                scoring.score_field(ef, ocr_text)
        t["output"] = {"status": order.status.value,
                       "fields": {f.value: {"value": ef.value, "status": ef.status.value, "reasons": ef.reasons}
                                  for f, ef in order.fields.items()}}
    if failures:                                   # L5: any stage failure blocks auto-fill
        if order.status == OrderStatus.ready_to_fill:
            order.status = OrderStatus.needs_pharmacist
        for fl in failures:
            _audit(order, "pipeline", "fail_closed", fl)
    order.pick_ticket = (PickTicket(status=PickStatus.blocked, warnings=["refill denied by prescriber"])
                         if order.status == OrderStatus.denied_by_prescriber else pick_ticket(order))
    safety.gate(order)                             # L6: re-assert invariants
    if callback.draft(order):
        _audit(order, "callback", "drafted", "callback to prescriber drafted for red fields")
    _audit(order, "pipeline", "done", order.status.value)
    tracing.submit_order(order.id, "success" if order.status == OrderStatus.ready_to_fill else "failure",
                         summary=_summary(order))
    return order


def _new_order(fax_id: str, fax_path: Path, page: int) -> Order:
    o = Order(id=f"ORD-{uuid.uuid4().hex[:8]}", fax_file=fax_path.name, fax_id=fax_id, page=page)
    o.audit.append(AuditEvent(actor="pipeline", action="received", detail=f"{fax_path.name} ({fax_id})"))
    return o


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


def _reject(order: Order, actor: str, flags: list[str], notes: list[str] | None = None) -> Order:
    order.status = OrderStatus.rejected
    order.guardrail_flags = flags
    for n in notes or []:
        _audit(order, "ingest", "page", n)
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


# ---------- pharmacist resolve (safety layer L7) ----------

class ResolveRefused(Exception):
    """The pharmacist's value still fails checks and no override reason was given."""
    def __init__(self, failed: list[str]):
        super().__init__("; ".join(failed))
        self.failed = failed


def resolve(order: Order, field: FieldName, value: str, pharmacist: str, override_reason: str | None = None) -> Order:
    """Pharmacist confirms or corrects one field. The value is re-checked like any other:
    a failing check refuses the change unless the pharmacist types an override reason.
    Returns a NEW order (the stored one is untouched if refused)."""
    ocr_text = OCR_TEXT.get(order.id, "")
    new = order.model_copy(deep=True)
    ef = new.fields[field]
    old = ef.value
    ef.value, ef.status, ef.confidence = value, Status.confirmed, 1.0
    ef.readings.append(Reading(source=f"pharmacist:{pharmacist}", value=value, confidence=1.0))

    with tracing.step("resolve", new.id, input_obj={"field": field.value, "value": value, "by": pharmacist}) as t:
        from backend import verify
        verify.reset(new)
        verify.run_all(new, ocr_text)
        failed = [c.detail for c in new.fields[field].checks if not c.passed]
        failed += [f"{c.detail} (on {f.value})" for f, other in new.fields.items() if f != field and other.status ==
                   Status.confirmed for c in other.checks if not c.passed
                   and not any(r.startswith("pharmacist override") for r in other.reasons)]
        if failed and not (override_reason and override_reason.strip()):
            t["output"] = {"refused": failed}
            raise ResolveRefused(failed)
        action = "override" if failed else ("confirmed" if (old or "") == value else "corrected")
        detail = f"{field.value}: {old!r} -> {value!r}"
        if failed:
            detail += f"; overrode failed checks ({'; '.join(failed)}) because: {override_reason.strip()}"
            ef.reasons = [f"pharmacist override by {pharmacist}: {override_reason.strip()}"] + failed
        new.audit.append(AuditEvent(actor=f"pharmacist:{pharmacist}", action=action, detail=detail[:500],
                                    trace_id=t.get("trace_id")))
        scoring.score_order(new, ocr_text)
        new.pick_ticket = pick_ticket(new)
        safety.gate(new)
        new.callback_draft = None
        callback.draft(new)
        t["output"] = {"action": action, "status": new.status.value,
                       "pick": new.pick_ticket.status.value if new.pick_ticket else None}
    tracing.submit_order(new.id, "success" if new.status == OrderStatus.ready_to_fill else "failure",
                         summary=f"after {action} of {field.value} by {pharmacist}: {_summary(new)}")
    return new
