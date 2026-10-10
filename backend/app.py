"""
Lane: Backend
API for the review UI.  Run: uvicorn backend.app:app --reload
"""
import io
import json
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from backend import config, pipeline
from backend.models import FieldName, Order, OrderStatus

app = FastAPI(title="Verifill")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
ORDERS: dict[str, Order] = {}


@app.on_event("startup")
def _warm():
    from backend.extract import ground_ocr
    try:
        ground_ocr.warm()                 # load the OCR model once, not on the first fax
    except Exception:  # noqa: BLE001  missing engine -> grounding falls back to Tesseract text
        pass


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


@app.get("/health")
def health():
    from backend import tracing
    return {"ok": True, "prism": tracing.enabled(), "nicrron": bool(config.NICRRON_API_KEY),
            "offline": config.OFFLINE}


@app.get("/samples")
def samples():
    return json.loads((config.SAMPLES_DIR / "ground_truth.json").read_text())


@app.get("/samples/{name}")
def sample_image(name: str):
    p = config.SAMPLES_DIR / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


def _sample_path(fax_id: str) -> Path:
    hits = sorted(config.SAMPLES_DIR.glob(f"{Path(fax_id).name}_fax.*"))
    if not hits:
        raise HTTPException(404, "unknown sample")
    return hits[0]


def _save_upload(file: UploadFile) -> Path:
    tmp = Path(tempfile.mkdtemp()) / Path(file.filename or "fax.png").name
    with tmp.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return tmp


@app.post("/faxes/sample/{fax_id}", response_model=list[Order])
def fax_from_sample(fax_id: str):
    """Any fax -> every order on it (multi-drug pages give several; cover pages are skipped)."""
    return _run_fax(_sample_path(fax_id))


@app.post("/faxes/upload", response_model=list[Order])
def fax_from_upload(file: UploadFile):
    return _run_fax(_save_upload(file))


@app.post("/orders/sample/{fax_id}", response_model=Order)
def order_from_sample(fax_id: str):
    """Backwards-compatible: first order of the fax. Use /faxes/sample/{id} for all of them."""
    return _run_fax(_sample_path(fax_id))[0]


@app.post("/orders/upload", response_model=Order)
def order_from_upload(file: UploadFile):
    return _run_fax(_save_upload(file))[0]


@app.get("/orders/{order_id}/page.png")
def order_page(order_id: str):
    """The cleaned page the readers saw (rotated/dewarped); readings[].bbox refer to this image."""
    img = pipeline.PAGE_IMAGES.get(order_id)
    if img is None:
        raise HTTPException(404)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png")


@app.get("/orders/{order_id}", response_model=Order)
def get_order(order_id: str):
    if order_id not in ORDERS:
        raise HTTPException(404)
    return ORDERS[order_id]


class Resolve(BaseModel):
    field: FieldName
    value: str = Field(min_length=1)
    pharmacist: str = Field(min_length=2, description="who is confirming; recorded in the audit trail")
    override_reason: str | None = Field(None, description="required to confirm a value that still fails a check")


@app.post("/orders/{order_id}/resolve", response_model=Order)
def resolve(order_id: str, body: Resolve):
    """Pharmacist confirms/corrects one field. Re-runs verify, scoring, inventory and the safety gate.
    409 if the value still fails a check and no override_reason is given (details in the response)."""
    if order_id not in ORDERS:
        raise HTTPException(404)
    order = ORDERS[order_id]
    if order.status == OrderStatus.rejected:
        raise HTTPException(409, "order was rejected by the guardrail; it cannot be resolved field by field")
    try:
        ORDERS[order_id] = pipeline.resolve(order, body.field, body.value.strip(), body.pharmacist.strip(),
                                            body.override_reason)
    except pipeline.ResolveRefused as e:
        raise HTTPException(409, {"message": "value still fails checks; add override_reason to confirm anyway",
                                  "failed_checks": e.failed})
    return ORDERS[order_id]


class Scan(BaseModel):
    ndc: str


@app.post("/orders/{order_id}/scan")
def scan(order_id: str, body: Scan):
    """Bench scan: the bottle's NDC must equal the pick ticket's. Mismatch -> {match: false} with a loud detail."""
    if order_id not in ORDERS:
        raise HTTPException(404)
    from backend import inventory
    from backend.models import AuditEvent
    order = ORDERS[order_id]
    match, detail = inventory.scan_confirm(order, body.ndc)
    order.audit.append(AuditEvent(actor="bench", action="scan_ok" if match else "scan_mismatch",
                                  detail=f"scanned NDC {body.ndc}: {detail}"[:500]))
    return {"match": match, "detail": detail}


def _run_fax(path: Path) -> list[Order]:
    orders = pipeline.run_fax(path)
    for o in orders:
        ORDERS[o.id] = o
    return orders
