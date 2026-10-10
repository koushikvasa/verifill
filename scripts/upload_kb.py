"""
Upload Verifill's reference data to the PRISM Knowledge Base (rule 4: ground
claims in the KB, not in the model). Run once after .env is set:

    python -m scripts.upload_kb

Splits each JSON into small text docs so kb_search returns focused chunks
(one doc per drug, per prescriber, per look-alike pair, per inventory SKU).
"""
import json

from backend import config, tracing


def docs():
    d = json.loads((config.DATA_DIR / "drugs.json").read_text())
    for name, x in d["drugs"].items():
        yield (f"drug_{name.replace(' ', '_')}.txt",
               f"DRUG {name}. Marketed strengths (mg): {', '.join(str(s) for s in x['strengths_mg'])}. "
               f"Forms: {', '.join(x['forms'])}. Max daily dose: {x['max_daily_mg']} mg. Class: {x['class']}. "
               f"Indications: {', '.join(x['indications'])}.")
    yield ("drug_brands.txt", "BRAND TO GENERIC: " + "; ".join(f"{b} = {g}" for b, g in d["brands"].items()))
    for a, b in json.loads((config.DATA_DIR / "lasa_pairs.json").read_text())["pairs"]:
        yield (f"lasa_{a}_{b}.txt", f"LOOK-ALIKE SOUND-ALIKE PAIR (ISMP): {a} and {b} are commonly confused. Verify before dispensing.")
    for npi, p in json.loads((config.DATA_DIR / "prescribers.json").read_text())["prescribers"].items():
        yield (f"prescriber_{npi}.txt", f"PRESCRIBER NPI {npi}: {p['name']}, {p['credential']}, {p['specialty']}, "
                                        f"state {p['state']}, phone {p['phone']}, fax {p['fax']}.")
    for it in json.loads((config.DATA_DIR / "inventory.json").read_text())["items"]:
        yield (f"inventory_{it['id']}.txt",
               f"INVENTORY SKU {it['id']}: {it['drug']} {it['strength']} {it['dosage_form']}"
               f"{' (brand ' + it['brand'] + ')' if it['brand'] else ' (generic)'}, NDC {it['ndc']}, "
               f"lot {it['lot']}, expires {it['expiry']}, on hand {it['qty_on_hand']}, "
               f"location aisle {it['aisle']} shelf {it['shelf']} bin {it['bin']}.")


if __name__ == "__main__":
    pt = tracing.client()
    if pt is None:
        raise SystemExit("PRISM not configured: fill PRISMTRACE_* in .env")
    existing = {d.get("filename") for d in (pt.kb_list_documents() or [])}
    n = 0
    for fname, text in docs():
        if fname in existing:
            continue
        pt.kb_upload(fname, text, description="Verifill reference data")
        n += 1
    print(f"uploaded {n} docs ({len(existing)} already there)")
    print("test search:", pt.kb_search("levothyroxine marketed strengths max daily dose", limit=2))
