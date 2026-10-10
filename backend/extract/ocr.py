"""
Lane: Backend
Reader 0: Tesseract. Non-AI vote with per-word confidence + bounding boxes.

- preprocess(img): deskew (projection-profile search), median denoise, Otsu
- read_words(img): words {text, conf 0..1, bbox, line} on the ORIGINAL fax coordinates
- parse_fields(words, img=None) -> dict[FieldName, Reading] using the printed labels on
  the Rx pad. Labels are matched fuzzily (OCR turns "DOB" into "pop", "Date" into "pate"),
  with layout fallbacks when a label is destroyed.

Trust notes:
- Tesseract is overconfident on handwriting (it reads "7234567893" at 0.79), so this
  reader is one weak vote; scoring needs agreement with an independent reader.
- Words below JUNK_CONF are dropped; a field whose words are mostly junk returns
  value=None with its low confidence instead of garbage.
- Values are transcribed as read. No correction, no normalization here.
"""
import re
import sys

import cv2
import numpy as np
import pytesseract
from PIL import Image
from rapidfuzz import fuzz

from backend.models import FieldName, Reading

JUNK_CONF = 0.30       # word-level: below this a word is treated as noise
MIN_FIELD_CONF = 0.50  # field-level: below this the value is withheld (None)

STRENGTH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|units?|%)\b", re.I)

# field -> label tokens (normalized). The first token is the anchor.
LABELS: dict[str, list[str]] = {
    "patient_name": ["patient", "name"],
    "dob": ["dob"],
    "date_written": ["date"],
    "sig": ["sig"],
    "quantity": ["qty", "/", "disp", "#"],
    "days_supply": ["days", "supply"],
    "refills": ["refills"],
    "npi": ["npi"],
    "lic": ["lic"],            # not a field; ends the NPI value / locates the NPI row
}
# Short labels are too easy to fuzzy-match by accident ("ado" in a signature scrawl),
# so they only accept known OCR misreadings.
ALIASES = {"dob": {"dob", "d0b", "pop", "dos", "008", "d.o.b"}, "npi": {"npi", "pi", "np1"},
           "qty": {"qty", "oty", "aty", "gty"}, "sig": {"sig", "s1g", "sic"},
           "lic": {"lic", "lc", "tic", "uc", "lie"}}
RX_TOKENS = {"r", "r.", "rx", "r,", "re", "k"}


# ---------- image ----------

def _deskew_angle(gray: np.ndarray) -> float:
    ink = (gray < 128).astype(np.uint8)
    small = cv2.resize(ink, (ink.shape[1] // 4, ink.shape[0] // 4), interpolation=cv2.INTER_AREA)
    best, best_ang = -1.0, 0.0
    for ang in np.arange(-4, 4.01, 0.25):
        m = cv2.getRotationMatrix2D((small.shape[1] / 2, small.shape[0] / 2), ang, 1)
        score = cv2.warpAffine(small, m, small.shape[::-1]).sum(1).astype(float).var()
        if score > best:
            best, best_ang = score, float(ang)
    return best_ang


def preprocess(img: Image.Image) -> tuple[Image.Image, np.ndarray]:
    """Deskew, denoise, binarize. Returns (clean image, inverse affine to original coords)."""
    a = np.array(img.convert("L"))
    m = cv2.getRotationMatrix2D((a.shape[1] / 2, a.shape[0] / 2), _deskew_angle(a), 1)
    a = cv2.warpAffine(a, m, a.shape[::-1], flags=cv2.INTER_CUBIC, borderValue=255)
    a = cv2.medianBlur(a, 3)
    _, a = cv2.threshold(a, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return Image.fromarray(a), cv2.invertAffineTransform(m)


def _to_original(bbox: list[int], inv: np.ndarray) -> list[int]:
    x0, y0, x1, y1 = bbox
    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]], dtype=float) @ inv.T
    return [int(pts[:, 0].min()), int(pts[:, 1].min()), int(pts[:, 0].max()), int(pts[:, 1].max())]


def read_words(img: Image.Image, clean: bool = True) -> list[dict]:
    """OCR words with conf 0..1. bbox is on the original image; `pbox` on the clean one."""
    inv = None
    if clean:
        img, inv = preprocess(img)
    d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config="--psm 3")
    out = []
    for i, w in enumerate(d["text"]):
        w = "".join(ch for ch in w if ch.isprintable() and ord(ch) != 0xFFFD)
        if w.strip():
            pbox = [d["left"][i], d["top"][i], d["left"][i] + d["width"][i], d["top"][i] + d["height"][i]]
            out.append({"text": w, "conf": max(float(d["conf"][i]), 0) / 100, "pbox": pbox,
                        "bbox": _to_original(pbox, inv) if inv is not None else pbox,
                        "line": (d["block_num"][i], d["par_num"][i], d["line_num"][i])})
    read_words.last_clean = img if clean else None   # used by the DAW checkbox check
    return out


def text_of(words: list[dict]) -> str:
    """Raw OCR text in reading order (for the guardrail and pixel grounding)."""
    return "\n".join(" ".join(w["text"] for w in line) for line in _lines(words))


# ---------- parsing ----------

def _norm(t: str) -> str:
    t = t.lower().strip()
    return t if t in {"/", "#"} else re.sub(r"[^a-z0-9.#/-]", "", t).strip(".:")


def _lines(words: list[dict]) -> list[list[dict]]:
    """Group into visual rows by vertical centre (Tesseract sometimes splits a row)."""
    ws = sorted(words, key=lambda w: (w["pbox"][1] + w["pbox"][3]) / 2)
    rows: list[list[dict]] = []
    for w in ws:
        cy = (w["pbox"][1] + w["pbox"][3]) / 2
        if rows:
            r = rows[-1]
            rcy = sum((x["pbox"][1] + x["pbox"][3]) / 2 for x in r) / len(r)
            if abs(cy - rcy) < 22:
                r.append(w)
                continue
        rows.append([w])
    return [sorted(r, key=lambda w: w["pbox"][0]) for r in rows]


def _score(tok: str, label: str) -> float:
    if tok == label or tok in ALIASES.get(label, ()):
        return 100.0
    if len(label) <= 3:   # glued junk after a short label: "sig/", "Sigeet"
        return 90.0 if tok.startswith(label) and len(tok) <= len(label) + 3 else 0.0
    if len(tok) < 3:
        return 0.0
    r = fuzz.ratio(tok, label)
    return r if r >= 70 else 0.0


def _is(tok: str, label: str) -> bool:
    return _score(tok, label) > 0


def _find_labels(row: list[dict]) -> list[tuple[str, int, int]]:
    """[(field, start, end_exclusive)] of label spans in this row, left to right."""
    toks = [_norm(w["text"]) for w in row]
    found, i = [], 0
    while i < len(toks):
        hit, best = None, 0.0
        for field, lab in LABELS.items():   # best-scoring label wins ("pate" is Date, not Patient)
            sc = max(_score(toks[i], lab[0]), _score(toks[i], "disp") if field == "quantity" else 0)
            if sc > best:
                j = i + 1
                while j < len(toks) and any(_is(toks[j], t) for t in lab[1:]):
                    j += 1
                hit, best = (field, i, j), sc
        if hit:
            found.append(hit)
            i = hit[2]
        else:
            i += 1
    return found


def _reading(ws: list[dict]) -> Reading | None:
    if not ws:
        return None
    conf = sum(w["conf"] for w in ws) / len(ws)          # junk words drag confidence down
    keep = [w for w in ws if w["conf"] >= JUNK_CONF]
    value = " ".join(w["text"] for w in keep).strip() or None
    if conf < MIN_FIELD_CONF or not (value and re.search(r"[A-Za-z0-9]", value)):
        value = None
    boxes = [w["bbox"] for w in ws]
    bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
    return Reading(source="ocr", value=value, confidence=round(conf, 3), bbox=bbox)


def _split_rx(ws: list[dict]) -> dict[str, list[dict]]:
    """'Lisinopril 10 mg tablet' -> drug / strength / dosage_form word groups."""
    text_pos, pos = [], 0
    for w in ws:
        text_pos.append(pos)
        pos += len(w["text"]) + 1
    m = STRENGTH_RE.search(" ".join(w["text"] for w in ws))
    if not m:
        return {"drug": ws}
    s_words = [w for w, p in zip(ws, text_pos) if m.start() <= p < m.end()]
    before = [w for w, p in zip(ws, text_pos) if p < m.start()]
    after = [w for w, p in zip(ws, text_pos) if p >= m.end()]
    return {"drug": before, "strength": s_words, "dosage_form": after}


def _daw(img: Image.Image | None, rows: list[list[dict]]) -> Reading | None:
    """Best effort: compare ink inside the box left of 'Dispense' vs left of 'Substitution'."""
    if img is None:
        return None
    a = np.array(img.convert("L"))
    boxes = {}
    for row in rows:
        for w in row:
            t = _norm(w["text"])
            for key, label in (("daw", "dispense"), ("sub", "substitution")):
                if _is(t, label):
                    x0, y0, x1, y1 = w["pbox"]
                    h = y1 - y0
                    region = a[max(y0 - h // 2, 0): y1 + h // 2, max(x0 - int(2.4 * h), 0): max(x0 - h // 5, 0)]
                    if region.size:
                        boxes[key] = ((region < 128).mean(), w["bbox"])
    if set(boxes) != {"daw", "sub"}:
        return None
    (d_ink, d_box), (s_ink, _) = boxes["daw"], boxes["sub"]
    if max(d_ink, s_ink) == 0:
        return None
    ratio = max(d_ink, s_ink) / max(min(d_ink, s_ink), 1e-3)
    if ratio < 1.3:
        return Reading(source="ocr", value=None, confidence=0.3, bbox=d_box)
    return Reading(source="ocr", value="true" if d_ink > s_ink else "false",
                   confidence=round(min(0.5 + ratio / 10, 0.9), 3), bbox=d_box)


def parse_fields(words: list[dict], img: Image.Image | None = None) -> dict[FieldName, Reading | None]:
    """Every FieldName -> Reading (or None if the label/value wasn't found)."""
    rows = _lines(words)
    out: dict[str, Reading | None] = {f.value: None for f in FieldName}
    at: dict[str, int] = {}   # field/label -> row index where its label was found

    for ri, row in enumerate(rows):
        spans = _find_labels(row)
        # "DOB" is often misread ("pop", "dos"): on the Date row, a leading unlabeled word is the DOB label
        if spans and spans[0][0] == "date_written" and spans[0][1] >= 2:
            spans.insert(0, ("dob", 0, 1))
        for k, (field, s, e) in enumerate(spans):
            at.setdefault(field, ri)
            end = spans[k + 1][1] if k + 1 < len(spans) else len(row)
            if field != "lic" and out.get(field) is None:
                out[field] = _reading(row[e:end])

    # Rx line: row starting with an "Rx" glyph; else the row above "Sig:";
    # else (labels destroyed) the first row between the DOB/Date row and the Qty row.
    top = max([at[f] for f in ("patient_name", "dob", "date_written") if f in at], default=-1)
    bottom = min([at[f] for f in ("sig", "quantity", "days_supply", "refills") if f in at], default=len(rows))
    rx_row = next((r for r in rows[top + 1: bottom] if _norm(r[0]["text"]) in RX_TOKENS and len(r) > 1), None)
    if rx_row is None and "sig" in at and at["sig"] - 1 > top:
        rx_row = rows[at["sig"] - 1]
    if rx_row is None and top >= 0 and top + 1 < bottom:
        rx_row = rows[top + 1]
    if rx_row:
        ws = rx_row[1:] if _norm(rx_row[0]["text"]) in RX_TOKENS else rx_row
        for field, group in _split_rx(ws).items():
            out[field] = _reading(group)

    # NPI row: labelled "NPI", else the row holding the "Lic" label (NPI value = words left of it)
    npi_row = at.get("npi", at.get("lic"))
    if out["npi"] is None and "lic" in at:
        row = rows[at["lic"]]
        li = next(i for i, w in enumerate(row) if _is(_norm(w["text"]), "lic"))
        ws = row[:li]
        if len(ws) > 1 and not re.search(r"\d", ws[0]["text"]):
            ws = ws[1:]                     # drop the garbled "NPI:" label
        out["npi"] = _reading(ws)

    # Prescriber name: printed row right above the NPI row
    if npi_row:
        for r in reversed(rows[max(npi_row - 2, 0): npi_row]):
            if not _find_labels(r) and not any(_is(_norm(w["text"]), "signature") for w in r):
                out["prescriber_name"] = _reading(r)
                break

    out["daw"] = _daw(img, rows)
    return {FieldName(k): v for k, v in out.items()}


def read(img: Image.Image) -> tuple[str, dict[FieldName, Reading | None], list[dict]]:
    """One call for the pipeline: (raw text, fields, words)."""
    words = read_words(img)
    return text_of(words), parse_fields(words, read_words.last_clean), words


if __name__ == "__main__":
    from backend import config  # noqa: F401  (sets the Tesseract path on Windows)
    for path in sys.argv[1:]:
        _, fields, _ = read(Image.open(path))
        print(f"== {path}")
        for f, r in fields.items():
            print(f"  {f.value:16} {('-' if r is None else repr(r.value)):32} "
                  f"conf={'' if r is None else r.confidence}  bbox={'' if r is None else r.bbox}")
