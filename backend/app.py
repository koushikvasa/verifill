"""
Lane: Backend
API for the review UI.  Run: uvicorn backend.app:app --reload
"""
import json
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from backend import config, pipeline
from backend.models import FieldName, Order, OrderStatus

app = FastAPI(title="Verifill")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
ORDERS: dict[str, Order] = {}


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


@app.post("/orders/sample/{fax_id}", response_model=Order)
def order_from_sample(fax_id: str):
    p = config.SAMPLES_DIR / f"{fax_id}_fax.png"
    if not p.exists():
        raise HTTPException(404, "unknown sample")
    return _run(p)


@app.post("/orders/upload", response_model=Order)
def order_from_upload(file: UploadFile):
    tmp = Path(tempfile.mkdtemp()) / Path(file.filename or "fax.png").name
    with tmp.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return _run(tmp)


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
    """TODO: compare scanned NDC to pick_ticket.item.ndc; mismatch -> loud warning."""
    raise HTTPException(501, "not implemented yet")


def _run(path: Path) -> Order:
    try:
        order = pipeline.run(path)
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    ORDERS[order.id] = order
    return order
