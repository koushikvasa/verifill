"""
Lane: Backend
Orchestrator. One fax in -> one Order out. Each stage is traced in PRISM
with its own agent_name (verifill-<stage>), session_id = order id.

fax -> guard -> readers (ocr + reader_a + reader_b) -> zoom (low conf only)
    -> verify (KB/RxNav/NPPES + LASA + rules) -> score -> inventory (only if
    drug verified) -> callback draft (red fields) -> Order
"""
import uuid
from pathlib import Path

from PIL import Image

from backend import guard
from backend.models import AuditEvent, Order, OrderStatus


def run(fax_path: Path) -> Order:
    order = Order(id=f"ORD-{uuid.uuid4().hex[:8]}", fax_file=fax_path.name)
    img = Image.open(fax_path).convert("L")
    order.audit.append(AuditEvent(actor="pipeline", action="received", detail=fax_path.name))

    # 1. guardrail on raw OCR text BEFORE any model sees it
    import pytesseract
    raw_text = pytesseract.image_to_string(img)
    order.guardrail_flags = guard.check(raw_text, order.id)
    if order.guardrail_flags:
        order.status = OrderStatus.rejected
        order.audit.append(AuditEvent(actor="guard", action="flagged", detail="; ".join(order.guardrail_flags)))
        return order

    # 2. readers            -> backend/extract/ocr.py, vision.py
    # 3. zoom-and-reread    -> backend/extract/zoom.py
    # 4. verify             -> backend/verify/{rxnorm,npi,lasa,rules}.py
    # 5. score              -> backend/scoring.py
    # 6. inventory          -> backend/inventory.py (only if drug/strength/form green)
    # 7. callback draft     -> red fields -> message to prescriber (phone from registry)
    raise NotImplementedError("stages 2-7: see TODOs in each module")
