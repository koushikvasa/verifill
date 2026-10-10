"""Every `_paths` case in backend/data/inventory.json. Offline; today is frozen to the hackathon date."""
from datetime import date

from backend import inventory
from backend.models import ExtractedField, FieldName as F, Order, PickStatus, Status

TODAY = date(2026, 10, 10)


def order(drug, strength, form="tablet", qty="30", days="30", daw="false", drug_status=Status.verified) -> Order:
    o = Order(id="T", fax_file="t.png")
    vals = {F.drug: drug, F.strength: strength, F.dosage_form: form, F.quantity: qty, F.days_supply: days, F.daw: daw}
    for f in F:
        o.fields[f] = ExtractedField(name=f, value=vals.get(f, "x"),
                                     status=drug_status if f == F.drug else Status.verified)
    return o


def pick(*a, **k):
    return inventory.build_pick_ticket(order(*a, **k), today=TODAY)


def test_rx001_lisinopril_in_stock():
    pt = pick("Lisinopril", "10 mg")
    assert pt.status == PickStatus.in_stock and pt.item.lot == "LS2408A"
    assert (pt.item.aisle, pt.item.shelf, pt.item.bin) == ("3", "B", "12") and pt.qty_to_pick == 30


def test_rx002_metformin_partial():
    pt = pick("Metformin", "500 mg", qty="#60 (sixty)")
    assert pt.status == PickStatus.partial and pt.qty_to_pick == 40 and pt.owed == 20


def test_rx003_ambiguous_drug_is_blocked_even_though_both_in_stock():
    pt = pick("Hydroxyzine", "25 mg", drug_status=Status.conflict)
    assert pt.status == PickStatus.blocked and pt.item is None


def test_rx004_wrong_strength_is_never_substituted():
    pt = pick("Levothyroxine", "0.5 mg", drug_status=Status.confirmed)
    assert pt.status == PickStatus.out_of_stock and pt.substitution is None
    assert any("0.05 mg" in w for w in pt.warnings)


def test_rx005_daw_generic_written_no_brand_swap():
    pt = pick("Amlodipine", "10 mg", daw="true")
    assert pt.status == PickStatus.in_stock and pt.item.brand is None and pt.substitution is None


def test_daw_brand_written_gets_that_brand():
    pt = pick("Norvasc", "10 mg", daw="true")
    assert pt.status == PickStatus.in_stock and pt.item.brand == "Norvasc"


def test_rx007_sertraline_expired_skipped_brand_offered_with_approval():
    pt = pick("Sertraline", "50 mg")
    assert pt.status == PickStatus.out_of_stock and pt.item is None
    assert any("SR2409E" in w and "EXPIRED" in w for w in pt.warnings)
    assert pt.substitution and pt.substitution.brand == "Zoloft" and pt.substitution_requires_approval


def test_rx007_sertraline_daw_no_substitution():
    pt = pick("Sertraline", "50 mg", daw="true")
    assert pt.status == PickStatus.out_of_stock and pt.substitution is None
    assert any("no substitution" in w for w in pt.warnings)


def test_rx008_glipizide_fefo_skips_lot_expiring_before_supply_ends():
    pt = pick("Glipizide", "5 mg", qty="30", days="30")
    assert pt.status == PickStatus.in_stock and pt.item.lot == "GP2606C"
    assert any("GP2510A" in w for w in pt.warnings)


def test_rx011_three_drugs_three_tickets():
    for d, s in (("Metformin", "1000 mg"), ("Lisinopril", "20 mg"), ("Atorvastatin", "20 mg")):
        assert pick(d, s).status == PickStatus.in_stock


def test_rx012_prednisone_location():
    pt = pick("Prednisone", "20 mg", qty="10", days="5")
    assert pt.status == PickStatus.in_stock and (pt.item.aisle, pt.item.shelf, pt.item.bin) == ("5", "A", "08")


def test_rx019_oxycodone_only_after_pharmacist_confirmation():
    assert pick("Oxycodone", "5 mg", qty="20", days="5", drug_status=Status.conflict).status == PickStatus.blocked
    pt = pick("Oxycodone", "5 mg", qty="20", days="5", drug_status=Status.confirmed)
    assert pt.status == PickStatus.in_stock and any("CII safe" in w for w in pt.warnings)


def test_scan_confirm():
    o = order("Lisinopril", "10 mg")
    o.pick_ticket = inventory.build_pick_ticket(o, today=TODAY)
    assert inventory.scan_confirm(o, "99101-0110-30")[0]
    ok, detail = inventory.scan_confirm(o, "99101-0120-30")
    assert not ok and "WRONG PRODUCT" in detail and "20 mg" in detail
    blocked = order("Hydroxyzine", "25 mg", drug_status=Status.conflict)
    blocked.pick_ticket = inventory.build_pick_ticket(blocked, today=TODAY)
    assert not inventory.scan_confirm(blocked, "99103-0025-01")[0]
