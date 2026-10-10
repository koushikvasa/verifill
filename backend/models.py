"""
Shared data contract. Every lane codes against these types, so agree on them
first and change them only with a heads-up to the team.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FieldName(str, Enum):
    patient_name = "patient_name"
    dob = "dob"
    date_written = "date_written"
    drug = "drug"
    strength = "strength"
    dosage_form = "dosage_form"
    sig = "sig"
    quantity = "quantity"
    days_supply = "days_supply"
    refills = "refills"
    daw = "daw"
    prescriber_name = "prescriber_name"
    npi = "npi"


# fields that must be green before an order can be auto-filled
CRITICAL_FIELDS = {
    FieldName.patient_name, FieldName.dob, FieldName.drug, FieldName.strength,
    FieldName.sig, FieldName.quantity, FieldName.npi,
}


class Status(str, Enum):
    verified = "verified"        # green: readers agree AND checks pass
    review = "review"            # amber: low confidence or single source
    conflict = "conflict"        # red: readers disagree or a check failed
    confirmed = "confirmed"      # pharmacist confirmed/corrected it


class Reading(BaseModel):
    """One reader's opinion of one field."""
    source: str                  # "ocr" | "reader_a:<model>" | "reader_b:<model>" | "zoom"
    value: Optional[str] = None
    confidence: Optional[float] = None   # 0..1 when the source reports one
    bbox: Optional[list[int]] = None     # [x0, y0, x1, y1] on the fax image


class Check(BaseModel):
    """One verification or rule check against grounded data."""
    name: str                    # e.g. "rxnorm_strength", "npi_luhn", "lasa", "max_daily_dose"
    passed: bool
    detail: str                  # human-readable reason shown in the UI
    grounded_in: Optional[str] = None   # "prism_kb" | "rxnav" | "nppes" | "local"


class ExtractedField(BaseModel):
    name: FieldName
    value: Optional[str] = None
    status: Status = Status.review
    confidence: float = 0.0
    readings: list[Reading] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)   # why this color
    candidates: list[str] = Field(default_factory=list)  # e.g. look-alike drug options


class AuditEvent(BaseModel):
    at: datetime = Field(default_factory=datetime.utcnow)
    actor: str                   # "pipeline" | "pharmacist" | step name
    action: str
    detail: str = ""
    trace_id: Optional[str] = None   # PRISM trace id when available


class InventoryItem(BaseModel):
    id: int
    ndc: str
    drug: str
    strength: str
    dosage_form: str
    brand: Optional[str] = None
    is_generic: bool = True
    manufacturer: str
    lot: str
    expiry: date
    qty_on_hand: int
    aisle: str
    shelf: str
    bin: str


class PickStatus(str, Enum):
    in_stock = "in_stock"
    partial = "partial"
    out_of_stock = "out_of_stock"
    blocked = "blocked"          # drug not verified yet: inventory refuses to run


class PickTicket(BaseModel):
    status: PickStatus
    item: Optional[InventoryItem] = None
    qty_needed: int = 0
    qty_to_pick: int = 0
    owed: int = 0
    substitution: Optional[InventoryItem] = None
    substitution_requires_approval: bool = False
    warnings: list[str] = Field(default_factory=list)


class OrderStatus(str, Enum):
    ready_to_fill = "ready_to_fill"
    needs_pharmacist = "needs_pharmacist"
    rejected = "rejected"        # guardrail blocked or unreadable


class Order(BaseModel):
    id: str
    fax_file: str
    status: OrderStatus = OrderStatus.needs_pharmacist
    fields: dict[FieldName, ExtractedField] = Field(default_factory=dict)
    guardrail_flags: list[str] = Field(default_factory=list)
    pick_ticket: Optional[PickTicket] = None
    callback_draft: Optional[str] = None
    audit: list[AuditEvent] = Field(default_factory=list)
