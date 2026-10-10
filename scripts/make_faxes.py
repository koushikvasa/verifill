"""
Verifill — synthetic prescriber-to-pharmacy fax generator.

Renders handwritten prescriptions on a clinic Rx pad, then degrades them the way
real faxes break: 1-bit thresholding, standard-mode (204x98 dpi) vertical
squash, skew, blur, scanner streaks, transmission dropout rows, toner fade,
smudges over specific fields, edge crops, stamps and fax-of-a-fax.

Every image ships with ground truth (what the prescriber actually wrote) and
the flags a careful pharmacist SHOULD raise, so you can score your pipeline.

All patients, prescribers, phone numbers and NPIs are fictional.

Usage:  python make_faxes.py --out ../samples/faxes --fonts ../fonts
"""
import argparse, json, math, random, zlib
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H = 1700, 2200  # US letter at 200 dpi

CLINICS = {
    "okafor": {"clinic": "RIVERSIDE INTERNAL MEDICINE", "addr": "410 W 116th St, Suite 3, New York, NY 10027",
               "phone": "(212) 555-0142", "fax": "(212) 555-0143",
               "prescriber": "Maya Okafor, MD", "npi": "1234567893", "lic": "NY 284113"},
    "reyes":  {"clinic": "BAY RIDGE FAMILY HEALTH", "addr": "7215 5th Ave, Brooklyn, NY 11209",
               "phone": "(718) 555-0198", "fax": "(718) 555-0199",
               "prescriber": "Daniel Reyes, DO", "npi": "1987654328", "lic": "NY 301957"},
    "raman":  {"clinic": "HUDSON ENDOCRINE ASSOCIATES", "addr": "88 River St, Hoboken, NJ 07030",
               "phone": "(201) 555-0110", "fax": "(201) 555-0111",
               "prescriber": "Priya Raman, MD", "npi": "1452637899", "lic": "NJ 25MA0918"},
    "chen":   {"clinic": "CHELSEA DERMATOLOGY GROUP", "addr": "201 W 23rd St, New York, NY 10011",
               "phone": "(646) 555-0177", "fax": "(646) 555-0178",
               "prescriber": "Samuel Chen, MD", "npi": "1765432103", "lic": "NY 296620"},
}

# what's written on the pad (ground truth), how badly the fax is damaged,
# and what a careful pharmacist should flag
CASES = [
    dict(id="RX-001", clinic="okafor", hand="Caveat", severity="mild",
         patient="Angela Moreno", dob="03/14/1962", date="10/09/2026",
         drug="Lisinopril", strength="10 mg", form="tablet", sig="1 tab PO once daily for BP",
         qty="30", days="30", refills="3", daw=False,
         damage=["threshold", "skew:1.2", "speckle:0.002"],
         expected_flags=[],
         note="Baseline. Should go all green and straight to pick ticket."),
    dict(id="RX-002", clinic="reyes", hand="ReenieBeanie", severity="bad",
         patient="Terrence Walsh", dob="11/02/1955", date="10/08/2026",
         drug="Metformin", strength="500 mg", form="tablet", sig="1 tab PO BID with meals",
         qty="60", days="30", refills="5", daw=False,
         damage=["blur:2.2", "standard_mode", "threshold", "streaks:3", "dropout:random:3", "skew:-2.0", "speckle:0.015"],
         expected_flags=["low OCR confidence across handwriting"],
         note="Scanner streaks + standard-resolution squash. Readable by a human, hard for OCR."),
    dict(id="RX-003", clinic="chen", hand="Caveat", severity="bad",
         patient="Lucia Ferreira", dob="07/21/1990", date="10/09/2026",
         drug="Hydroxyzine", strength="25 mg", form="tablet", sig="1 tab PO q6h PRN itching",
         qty="30", days="8", refills="0", daw=False,
         damage=["smudge:drug", "blur:1.5", "threshold", "skew:1.8", "speckle:0.012", "scan_look"],
         expected_flags=["drug name partly obscured", "look-alike risk: hydroxyzine vs hydralazine",
                         "indication 'itching' supports hydroxyzine; confirm with prescriber or pharmacist"],
         note="Smudge sits over the middle of the drug name. The look-alike guard must fire; inventory must NOT pick one."),
    dict(id="RX-004", clinic="raman", hand="HomemadeApple", severity="bad",
         patient="Robert Kim", dob="01/30/1971", date="10/07/2026",
         drug="Levothyroxine", strength="0.5 mg", form="tablet", sig="1 tab PO daily before breakfast",
         qty="30", days="30", refills="2", daw=False,
         damage=["blur:1.4", "standard_mode", "threshold", "fax_of_fax", "skew:-1.4", "speckle:0.012"],
         expected_flags=["dose 0.5 mg exceeds max daily 0.3 mg; likely decimal error for 0.05 mg (50 mcg)",
                         "0.5 mg is not a marketed levothyroxine strength", "callback to prescriber required"],
         note="Prescriber wrote 0.5 mg (probably meant 0.05 mg). Reading it correctly is NOT enough: it must be caught."),
    dict(id="RX-005", clinic="okafor", hand="Caveat", severity="bad",
         patient="Denise Odum", dob="09/09/1948", date="10/09/2026",
         drug="Amlodipine", strength="10 mg", form="tablet", sig="1 tab PO once daily",
         qty="90", days="90", refills="1", daw=True,
         damage=["blur:1.2", "threshold", "dropout:qty", "dropout:random:4", "skew:0.9", "speckle:0.010"],
         expected_flags=["quantity line damaged by transmission dropout", "dispense as written: no generic substitution"],
         note="Horizontal dropout rows shear through the quantity. DAW box is checked."),
    dict(id="RX-006", clinic="reyes", hand="HomemadeApple", severity="very bad",
         patient="Marcus Bell", dob="05/17/1983", date="10/08/2026",
         drug="Clonidine", strength="0.1 mg", form="tablet", sig="1 tab PO BID for BP",
         qty="60", days="30", refills="2", daw=False, npi_override="1987654382",
         damage=["blur:2.0", "standard_mode", "threshold", "streaks:2", "skew:2.6", "fade:bottom", "speckle:0.015"],
         expected_flags=["look-alike risk: clonidine vs clonazepam (messy script)",
                         "NPI 1987654382 fails check digit (likely transposed: 1987654328)",
                         "verify prescriber identity before filling"],
         note="Messy handwriting, look-alike drug, and the NPI on the pad has two digits transposed."),
    dict(id="RX-007", clinic="okafor", hand="ReenieBeanie", severity="bad",
         patient="Hannah Lieberman", dob="12/03/1999", date="10/09/2026",
         drug="Sertraline", strength="50 mg", form="tablet", sig="1 tab PO once daily",
         qty="90", days="30", refills="0", daw=False,
         damage=["blur:1.3", "threshold", "fade:right", "skew:-1.1", "speckle:0.010", "scan_look"],
         expected_flags=["quantity 90 vs 1 tab/day x 30 days = 30; mismatch",
                         "right side faded: refills/days fields low confidence"],
         note="Toner fade on the right half, and qty doesn't match sig x days supply."),
    dict(id="RX-008", clinic="raman", hand="NothingYouCouldDo", severity="extreme",
         patient="Gloria Asante", dob="04/25/1958", date="10/06/2026",
         drug="Glipizide", strength="5 mg", form="tablet", sig="1 tab PO daily with breakfast",
         qty="30", days="30", refills="3", daw=False,
         damage=["blur:1.8", "standard_mode", "threshold", "fax_of_fax", "streaks:4", "dropout:random:6",
                 "skew:3.8", "crop:right", "stamp", "fade:bottom", "speckle:0.012"],
         expected_flags=["look-alike risk: glipizide vs glyburide", "multiple fields unreadable",
                         "should NOT auto-fill; route whole order to pharmacist / callback"],
         note="Worst case. Correct behavior is to refuse to auto-fill, not to produce a confident order."),
]


def font(path, size):
    return ImageFont.truetype(str(path), size)


def render(case, fonts_dir):
    c = CLINICS[case["clinic"]]
    rng = random.Random(case["id"])
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    sans = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    sans_b = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    serif_b = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
    mono = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
    hand_file = {"Caveat": "Caveat.ttf", "HomemadeApple": "HomemadeApple-Regular.ttf",
                 "ReenieBeanie": "ReenieBeanie.ttf", "NothingYouCouldDo": "NothingYouCouldDo.ttf"}[case["hand"]]
    hsize = {"Caveat": 66, "HomemadeApple": 46, "ReenieBeanie": 74, "NothingYouCouldDo": 54}[case["hand"]]
    hf = font(Path(fonts_dir) / hand_file, hsize)
    boxes = {}

    def hw(xy, text, key=None, jitter=6):
        x, y = xy
        x += rng.randint(-jitter, jitter); y += rng.randint(-jitter, jitter)
        d.text((x, y), text, font=hf, fill=rng.randint(0, 40))
        if key:
            boxes[key] = d.textbbox((x, y), text, font=hf)

    # fax transmission header (added by sending machine)
    d.text((40, 18), f"OCT {case['date'][3:5]} 2026 {rng.randint(8,17):02d}:{rng.randint(0,59):02d}   FROM: {c['fax']}   "
                     f"{c['clinic'][:24]}   TO: VERIFILL PHARMACY (212) 555-0100   P.01/01",
           font=font(mono, 26), fill=0)
    d.line((40, 58, W - 40, 58), fill=0, width=2)

    # letterhead
    d.text((120, 110), c["clinic"], font=font(serif_b, 58), fill=0)
    d.text((120, 190), c["addr"], font=font(sans, 32), fill=0)
    d.text((120, 236), f"Tel {c['phone']}     Fax {c['fax']}", font=font(sans, 32), fill=0)
    d.line((120, 300, W - 120, 300), fill=0, width=4)

    # patient block (pre-printed labels + handwriting)
    lab = font(sans_b, 34)
    rows = [("Patient Name", 380, "patient", case["patient"]), ("DOB", 470, "dob", case["dob"]),
            ("Date", 470, "date", case["date"])]
    d.text((120, 380), "Patient Name", font=lab, fill=0); d.line((400, 430, 1580, 430), fill=0, width=2)
    hw((420, 350), case["patient"], "patient")
    d.text((120, 480), "DOB", font=lab, fill=0); d.line((220, 530, 780, 530), fill=0, width=2)
    hw((240, 450), case["dob"], "dob")
    d.text((860, 480), "Date", font=lab, fill=0); d.line((960, 530, 1580, 530), fill=0, width=2)
    hw((980, 450), case["date"], "date")

    # Rx symbol
    d.text((120, 610), "R", font=font(serif_b, 130), fill=0)
    d.text((200, 680), "x", font=font(serif_b, 80), fill=0)

    # drug lines
    hw((330, 640), f"{case['drug']} {case['strength']} {case['form']}", "drug_line")
    d.line((320, 730, 1580, 730), fill=0, width=2)
    d.text((330, 780), "Sig:", font=lab, fill=0)
    hw((430, 750), case["sig"], "sig")
    d.line((420, 840, 1580, 840), fill=0, width=2)

    d.text((120, 930), "Qty / Disp #", font=lab, fill=0); d.line((380, 980, 760, 980), fill=0, width=2)
    hw((400, 900), f"#{case['qty']} ({_words(case['qty'])})", "qty")
    d.text((820, 930), "Days supply", font=lab, fill=0); d.line((1060, 980, 1580, 980), fill=0, width=2)
    hw((1080, 900), case["days"], "days")
    d.text((120, 1040), "Refills", font=lab, fill=0); d.line((290, 1090, 760, 1090), fill=0, width=2)
    hw((310, 1010), case["refills"], "refills")

    # substitution checkboxes
    y = 1170
    for i, label in enumerate(["Substitution permitted", "Dispense as written (DAW)"]):
        x = 120 + i * 760
        d.rectangle((x, y, x + 44, y + 44), outline=0, width=3)
        d.text((x + 64, y + 2), label, font=font(sans, 34), fill=0)
        checked = (i == 1) == case["daw"]
        if checked:
            d.line((x + 6, y + 22, x + 18, y + 40), fill=0, width=6)
            d.line((x + 18, y + 40, x + 52, y - 8), fill=0, width=6)
    boxes["daw"] = (120, y, 1600, y + 50)

    # signature scribble + prescriber block
    d.text((120, 1330), "Prescriber signature", font=lab, fill=0)
    d.line((520, 1380, 1580, 1380), fill=0, width=2)
    pts, x0 = [], 560
    for k in range(40):
        pts.append((x0 + k * 22 + rng.randint(-8, 8), 1340 + int(28 * math.sin(k * 0.9)) + rng.randint(-14, 14)))
    d.line(pts, fill=0, width=4)
    boxes["signature"] = (520, 1290, 1580, 1390)

    npi = case.get("npi_override", c["npi"])
    d.text((120, 1450), c["prescriber"], font=font(sans_b, 38), fill=0)
    d.text((120, 1505), f"NPI: ", font=font(sans, 34), fill=0)
    hw((210, 1470), npi, "npi")
    d.text((820, 1505), f"Lic: {c['lic']}", font=font(sans, 34), fill=0)
    boxes["prescriber"] = d.textbbox((120, 1450), c["prescriber"], font=font(sans_b, 38))

    # confidentiality footer
    foot = ("CONFIDENTIALITY NOTICE: This facsimile contains protected health information intended only for the "
            "named recipient. If received in error, notify the sender and destroy all copies.")
    _wrap(d, foot, (120, 2000), font(sans, 24), 1460)
    return img, boxes


def _words(n):
    w = {"0": "zero", "8": "eight", "30": "thirty", "60": "sixty", "90": "ninety"}
    return w.get(n, n)


def _wrap(d, text, xy, f, width):
    x, y = xy; line = ""
    for word in text.split():
        t = (line + " " + word).strip()
        if d.textlength(t, font=f) > width:
            d.text((x, y), line, font=f, fill=0); y += 32; line = word
        else:
            line = t
    d.text((x, y), line, font=f, fill=0)


# ---------------------------------------------------------------- degradation
def degrade(img, boxes, steps, seed):
    rng = np.random.default_rng(zlib.crc32(seed.encode()))
    a = img
    for step in steps:
        name, _, arg = step.partition(":")
        if name == "smudge":
            b = boxes[arg] if arg in boxes else boxes["drug_line"]
            if arg == "drug":
                b = boxes["drug_line"]
                x0, y0, x1, y1 = b
                cx = x0 + int((x1 - x0) * 0.2); cy = (y0 + y1) // 2  # middle of the drug name
                a = _blob(a, cx, cy, 70, 40, rng)
            else:
                x0, y0, x1, y1 = b
                a = _blob(a, (x0 + x1) // 2, (y0 + y1) // 2, (x1 - x0) // 3, 40, rng)
        elif name == "blur":
            a = a.filter(ImageFilter.GaussianBlur(float(arg)))
        elif name == "skew":
            a = a.rotate(float(arg), resample=Image.BICUBIC, expand=False, fillcolor=255)
        elif name == "standard_mode":  # 204x98 dpi: halve vertical resolution
            a = a.resize((W, H // 2), Image.BILINEAR).resize((W, H), Image.NEAREST)
        elif name == "threshold":
            arr = np.asarray(a).astype(np.float32)
            arr += rng.normal(0, 18, arr.shape)
            a = Image.fromarray(np.where(arr < 150, 0, 255).astype(np.uint8))
        elif name == "fax_of_fax":
            a = a.resize((W // 2, H // 2), Image.BILINEAR).filter(ImageFilter.GaussianBlur(0.8)).resize((W, H), Image.BILINEAR)
            arr = np.asarray(a).astype(np.float32) + rng.normal(0, 22, (H, W))
            a = Image.fromarray(np.where(arr < 140, 0, 255).astype(np.uint8))
        elif name == "streaks":
            arr = np.array(a)
            for _ in range(int(arg)):
                x = int(rng.integers(100, W - 100)); w = int(rng.integers(2, 7))
                arr[:, x:x + w] = 0 if rng.random() < 0.7 else 255
            a = Image.fromarray(arr)
        elif name == "dropout":
            arr = np.array(a)
            if arg == "qty":
                y0, y1 = boxes["qty"][1], boxes["qty"][3]
                for y in range(y0 + 10, y1, 9):
                    shift = int(rng.integers(30, 90))
                    arr[y:y + 4] = np.roll(arr[y:y + 4], shift, axis=1)
                arr[(y0 + y1) // 2:(y0 + y1) // 2 + 8] = 255
            else:
                for _ in range(int(arg.split(":")[-1])):
                    y = int(rng.integers(300, H - 300)); h = int(rng.integers(3, 12))
                    arr[y:y + h] = 255 if rng.random() < 0.5 else np.roll(arr[y:y + h], int(rng.integers(40, 160)), axis=1)
            a = Image.fromarray(arr)
        elif name == "fade":
            arr = np.array(a).astype(np.float32)
            if arg == "right":
                ramp = np.clip((np.arange(W) - W * 0.45) / (W * 0.4), 0, 1)[None, :]
            else:
                ramp = np.clip((np.arange(H) - H * 0.55) / (H * 0.3), 0, 1)[:, None]
            drop = rng.random(arr.shape) < ramp * 0.75
            arr[drop] = 255
            a = Image.fromarray(arr.astype(np.uint8))
        elif name == "crop":
            arr = np.array(a); arr[:, W - 160:] = 255
            arr[:, W - 170:W - 160] = 0  # scanner edge shadow
            a = Image.fromarray(arr)
        elif name == "stamp":
            st = Image.new("L", (900, 260), 255); sd = ImageDraw.Draw(st)
            sd.rectangle((6, 6, 894, 254), outline=0, width=10)
            sd.text((60, 40), "RECEIVED", font=font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 120), fill=0)
            sd.text((70, 180), "OCT 10 2026  FRONT DESK", font=font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 44), fill=0)
            st = st.rotate(-12, expand=True, fillcolor=255)
            mask = st.point(lambda p: 255 if p < 128 and rng.random() > 0.25 else 0)
            a = a.copy(); a.paste(0, (620, 1180), mask)
        elif name == "scan_look":  # faxed page printed, then re-scanned on a cheap flatbed
            arr = np.asarray(a.convert("L")).astype(np.float32) / 255.0
            yy, xx = np.mgrid[0:H, 0:W]
            paper = 232 - 30 * ((xx / W - 0.6) ** 2 + (yy / H - 0.4) ** 2)  # uneven lighting
            paper -= 70 * np.exp(-xx / 45.0)                                # spine shadow on left edge
            out = paper * arr + (45 + rng.normal(0, 12, (H, W))) * (1 - arr)
            out += rng.normal(0, 7, (H, W))
            g = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.9))
            dx = int(rng.integers(20, 50))
            g = g.transform((W, H), Image.QUAD, (dx, 0, 0, H, W, H - dx // 2, W - dx // 3, 0),
                            resample=Image.BILINEAR, fillcolor=60)
            import io
            buf = io.BytesIO(); g.save(buf, "JPEG", quality=30); buf.seek(0)
            a = Image.open(buf).convert("L")
        elif name == "speckle":
            arr = np.array(a); p = float(arg)
            m = rng.random(arr.shape)
            arr[m < p / 2] = 0; arr[(m >= p / 2) & (m < p)] = 255
            a = Image.fromarray(arr)
    return a


def _blob(img, cx, cy, rx, ry, rng):
    arr = np.array(img).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    dist = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
    noise = rng.random((H, W)) * 0.6
    mask = dist + noise < 1.0
    arr[mask] = np.minimum(arr[mask], rng.uniform(20, 90, mask.sum()))
    return Image.fromarray(arr.astype(np.uint8))


def contact_sheet(paths, out):
    thumbs = [Image.open(p).convert("L").resize((425, 550)) for p in paths]
    cols = 4; rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("L", (cols * 445 + 20, rows * 600 + 20), 235)
    d = ImageDraw.Draw(sheet); f = font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
    for i, (t, p) in enumerate(zip(thumbs, paths)):
        x = 20 + (i % cols) * 445; y = 20 + (i // cols) * 600
        sheet.paste(t, (x, y)); d.text((x, y + 556), Path(p).stem, font=f, fill=0)
    sheet.save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../samples/faxes")
    ap.add_argument("--fonts", default="../fonts")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    truth, pngs, pages = [], [], []
    for case in CASES:
        clean, boxes = render(case, args.fonts)
        clean.save(out / f"{case['id']}_clean.png")
        fax = degrade(clean, boxes, case["damage"], case["id"])
        fax = fax if "scan_look" in case["damage"] else fax.convert("1")
        p = out / f"{case['id']}_fax.png"
        fax.save(p, dpi=(200, 200))
        pngs.append(p); pages.append(fax.convert("L"))
        c = CLINICS[case["clinic"]]
        truth.append({
            "id": case["id"], "file": p.name, "severity": case["severity"], "damage": case["damage"],
            "ground_truth": {
                "patient_name": case["patient"], "dob": case["dob"], "date_written": case["date"],
                "drug": case["drug"].lower(), "strength": case["strength"], "dosage_form": case["form"],
                "sig": case["sig"], "quantity": int(case["qty"]), "days_supply": int(case["days"]),
                "refills": int(case["refills"]), "daw": case["daw"],
                "prescriber_name": c["prescriber"], "npi_as_written": case.get("npi_override", c["npi"]),
                "prescriber_phone": c["phone"], "prescriber_fax": c["fax"],
            },
            "expected_flags": case["expected_flags"],
            "expected_outcome": "ready_to_fill" if not case["expected_flags"] else "needs_pharmacist",
            "note": case["note"],
        })
    (out / "ground_truth.json").write_text(json.dumps(truth, indent=2))
    pages[0].save(out / "fax_inbox_all.pdf", save_all=True, append_images=pages[1:], resolution=200)
    contact_sheet(pngs, out / "contact_sheet.png")
    print(f"wrote {len(CASES)} faxes to {out}")


if __name__ == "__main__":
    main()
