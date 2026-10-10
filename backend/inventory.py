"""
Lane: Data (seed) + Backend (logic)
Inventory + pick ticket. Runs ONLY when drug, strength and form are green or
pharmacist-confirmed. Otherwise returns PickTicket(status="blocked").

TODO:
- match: same drug + strength + form (normalize "25 mg" vs "25mg")
- FEFO: pick soonest-expiring lot that doesn't expire before days_supply ends
- partial: qty_to_pick = on hand, owed = remainder
- out of stock + DAW false: offer same drug/strength/form from another
  manufacturer/brand as substitution, substitution_requires_approval=True
- DAW true: no substitution, warning instead
- scan_confirm(order, ndc): scanned NDC must equal pick_ticket.item.ndc
"""
import json
from datetime import date

from backend import config
from backend.models import InventoryItem


def load() -> list[InventoryItem]:
    rows = json.loads((config.DATA_DIR / "inventory.json").read_text())["items"]
    return [InventoryItem(**r) for r in rows]
