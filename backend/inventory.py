"""
Lane: Data (seed) + Backend (logic)
Inventory + pick ticket. Runs ONLY when drug, strength and form are green or
pharmacist-confirmed. Otherwise returns PickTicket(status="blocked").
Stock NEVER resolves an ambiguous drug: by the time this runs, the drug is settled.

Rules:
- match: same generic drug + strength + form (normalized); brands map to their generic
- expired lots are skipped; FEFO among lots that don't expire before the days supply ends
- partial: qty_to_pick = on hand in the chosen lot, owed = remainder
- DAW false + nothing in stock: offer the same drug/strength/form from another
  manufacturer/brand as a substitution, substitution_requires_approval=True
- DAW true: dispense exactly what was written (generic written -> generic only, brand
  written -> that brand only); no substitution, warning instead
- a different strength is never a substitution (0.05 mg is not 0.5 mg)
- scan_confirm(order, ndc): scanned NDC must equal pick_ticket.item.ndc
"""
import json
import re
from datetime import date, timedelta

from backend import config, scoring
from backend.models import FieldName, InventoryItem, Order, PickStatus, PickTicket, Status
from backend.verify import rules, rxnorm

GREEN = (Status.verified, Status.confirmed)


def load() -> list[InventoryItem]:
    rows = json.loads((config.DATA_DIR / "inventory.json").read_text())["items"]
    return [InventoryItem(**r) for r in rows]


def _val(order: Order, f: FieldName) -> str | None:
    ef = order.fields.get(f)
    return ef.value if ef and ef.status in GREEN else None


def build_pick_ticket(order: Order, today: date | None = None, items: list[InventoryItem] | None = None) -> PickTicket:
    today = today or date.today()
    not_green = [f.value for f in (FieldName.drug, FieldName.strength, FieldName.dosage_form)
                 if _val(order, f) is None]
    if not_green:
        return PickTicket(status=PickStatus.blocked,
                          warnings=[f"blocked until verified by pharmacist: {', '.join(not_green)}"])

    written = order.fields[FieldName.drug].value.strip()
    generic = rxnorm.canonical(written) or written.lower()
    brand_written = written.lower() if rxnorm.DRUGS["brands"].get(written.lower()) else None
    strength = scoring.normalize(FieldName.strength, order.fields[FieldName.strength].value)
    form = scoring.normalize(FieldName.dosage_form, order.fields[FieldName.dosage_form].value)
    daw = scoring.normalize(FieldName.daw, order.fields[FieldName.daw].value) == "true"
    qty = rules.number(order.fields[FieldName.quantity].value) or 0
    days = rules.number(order.fields[FieldName.days_supply].value) or 0
    needed_until = today + timedelta(days=days)
    warnings: list[str] = []

    same_drug = [i for i in (items or load())
                 if i.drug.lower() == generic and scoring.normalize(FieldName.dosage_form, i.dosage_form) == form]
    matches = [i for i in same_drug if scoring.normalize(FieldName.strength, i.strength) == strength]
    if not matches:
        others = sorted({i.strength for i in same_drug})
        warnings.append(f"no {generic} {strength} {form} product in inventory"
                        + (f"; other strengths on hand: {', '.join(others)} (a different strength is never a "
                           f"substitution; pharmacist decides)" if others else ""))
        return PickTicket(status=PickStatus.out_of_stock, qty_needed=qty, owed=qty, warnings=warnings)

    def as_written(i: InventoryItem) -> bool:
        return (i.brand or "").lower() == brand_written if brand_written else i.brand is None

    exact = [i for i in matches if as_written(i)]
    pool = exact if daw else (exact + [i for i in matches if i not in exact])   # DAW: only as written
    usable = []
    for i in sorted(pool, key=lambda i: i.expiry):
        if i.expiry < today:
            warnings.append(f"lot {i.lot} (NDC {i.ndc}) EXPIRED {i.expiry}: skipped")
        elif days and i.expiry < needed_until:
            warnings.append(f"lot {i.lot} expires {i.expiry}, before the {days}-day supply ends {needed_until}: skipped")
        elif i.qty_on_hand <= 0:
            warnings.append(f"lot {i.lot} (NDC {i.ndc}): 0 on hand")
        else:
            usable.append(i)
    if "CII" in (matches[0].aisle or ""):
        warnings.append("Schedule II: stored in the CII safe; pharmacist must approve before picking")

    primary = next((i for i in usable if as_written(i)), None)        # FEFO: usable is expiry-sorted
    if primary is None and not daw:
        sub = next((i for i in usable if not as_written(i)), None)
        if sub:
            return PickTicket(status=PickStatus.out_of_stock, qty_needed=qty, owed=qty, substitution=sub,
                              substitution_requires_approval=True,
                              warnings=warnings + [f"as-written product unavailable; {sub.brand or sub.manufacturer} "
                                                   f"{sub.drug} {sub.strength} (NDC {sub.ndc}) offered as substitution, "
                                                   f"pharmacist approval required"])
    if primary is None:
        if daw:
            warnings.append("dispense as written: no substitution allowed; order the written product")
        return PickTicket(status=PickStatus.out_of_stock, qty_needed=qty, owed=qty, warnings=warnings)

    to_pick = min(qty, primary.qty_on_hand) if qty else primary.qty_on_hand
    owed = max(qty - to_pick, 0)
    if owed:
        warnings.append(f"partial fill: {to_pick} of {qty} from lot {primary.lot}, {owed} owed")
    return PickTicket(status=PickStatus.partial if owed else PickStatus.in_stock, item=primary,
                      qty_needed=qty, qty_to_pick=to_pick, owed=owed, warnings=warnings)


def scan_confirm(order: Order, ndc: str) -> tuple[bool, str]:
    """Bottle scanned at the bench must be the exact product on the pick ticket."""
    pt = order.pick_ticket
    scanned = re.sub(r"\D", "", ndc)
    if pt is None or pt.status == PickStatus.blocked or pt.item is None:
        return False, "no pick ticket: the order is not cleared for filling"
    if scanned == re.sub(r"\D", "", pt.item.ndc):
        return True, f"match: {pt.item.drug} {pt.item.strength} {pt.item.dosage_form}, lot {pt.item.lot}, NDC {pt.item.ndc}"
    if pt.substitution and scanned == re.sub(r"\D", "", pt.substitution.ndc):
        return False, f"scanned the SUBSTITUTION (NDC {pt.substitution.ndc}); it needs pharmacist approval first"
    hit = next((i for i in load() if re.sub(r"\D", "", i.ndc) == scanned), None)
    what = f"{hit.drug} {hit.strength} {hit.dosage_form} (lot {hit.lot})" if hit else "an unknown product"
    return False, f"WRONG PRODUCT: scanned {what}, NDC {ndc}; pick ticket says NDC {pt.item.ndc}. Do not dispense."
