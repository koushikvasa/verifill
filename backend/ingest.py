"""
Lane: Backend
Stage 0: any fax in. Splits pages, fixes orientation, dewarps phone photos, and
classifies each page (cheap, explainable keyword vote; the vision readers give a
second opinion in pipeline.py). See docs/FAX_VARIETY.md.

Every transform is returned as a human-readable note so the pipeline can audit it.
Never silently drops a page that might be a prescription: classification
disagreement -> new_rx (the pharmacist sees it).
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image

from backend import config  # noqa: F401  (Tesseract path on Windows)
from backend.models import FaxType


@dataclass
class Page:
    number: int                      # 1-based
    image: Image.Image               # cleaned page sent to OCR + readers
    rotated: int = 0                 # degrees applied (0/90/180/270)
    dewarped: bool = False
    notes: list[str] = field(default_factory=list)


def load_pages(path: Path) -> list[Image.Image]:
    """png/jpg/tif/pdf -> list of grayscale page images (PDF rendered at 200 dpi)."""
    if path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))
        return [pdf[i].render(scale=200 / 72).to_pil().convert("L") for i in range(len(pdf))]
    img = Image.open(path)
    pages = []
    for i in range(getattr(img, "n_frames", 1)):       # multi-page TIFF faxes
        img.seek(i)
        pages.append(img.convert("L"))
    return pages


def fix_orientation(img: Image.Image) -> tuple[Image.Image, int]:
    """Tesseract OSD; returns (upright image, degrees rotated). No-op on low-text pages."""
    try:
        osd = pytesseract.image_to_osd(img, config="--psm 0")
        rot = int(re.search(r"Rotate:\s*(\d+)", osd).group(1))
        conf = float(re.search(r"Orientation confidence:\s*([\d.]+)", osd).group(1))
    except (pytesseract.TesseractError, AttributeError, ValueError):
        return img, 0
    if rot == 0 or conf < 2.0:          # low-confidence OSD is noise on sparse pages
        return img, 0
    # OSD "Rotate" = clockwise degrees needed; PIL rotates counter-clockwise
    return img.rotate(-rot, expand=True, fillcolor=255), rot


def dewarp(img: Image.Image) -> tuple[Image.Image, bool]:
    """Phone photo of a page: largest 4-point contour covering >40% of the image -> warp flat."""
    a = np.array(img.convert("L"))
    h, w = a.shape
    edges = cv2.Canny(cv2.GaussianBlur(a, (5, 5), 0), 50, 150)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        area = cv2.contourArea(c)
        if area < 0.4 * h * w or area > 0.97 * h * w:   # full-frame contour = already a flat scan
            continue
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) != 4:
            continue
        pts = _order(approx.reshape(4, 2).astype("float32"))
        tw = int(max(np.linalg.norm(pts[1] - pts[0]), np.linalg.norm(pts[2] - pts[3])))
        th = int(max(np.linalg.norm(pts[3] - pts[0]), np.linalg.norm(pts[2] - pts[1])))
        scale = 2200 / max(th, 1)                       # normalize to fax-page height
        tw, th = int(tw * scale), int(th * scale)
        m = cv2.getPerspectiveTransform(pts, np.float32([[0, 0], [tw, 0], [tw, th], [0, th]]))
        flat = cv2.warpPerspective(a, m, (tw, th), borderValue=255)
        flat = cv2.adaptiveThreshold(flat, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
        return Image.fromarray(flat), True
    return img, False


def _order(p: np.ndarray) -> np.ndarray:
    s, d = p.sum(1), np.diff(p, axis=1).ravel()
    return np.array([p[np.argmin(s)], p[np.argmin(d)], p[np.argmax(s)], p[np.argmax(d)]], dtype="float32")


def prepare(path: Path) -> list[Page]:
    pages = []
    for i, raw in enumerate(load_pages(path), start=1):
        notes = []
        img, warped = dewarp(raw)
        if warped:
            notes.append(f"page {i}: photo detected, perspective corrected")
        img, rot = fix_orientation(img)
        if rot:
            notes.append(f"page {i}: rotated {rot} degrees (fax was fed {'upside down' if rot == 180 else 'sideways'})")
        pages.append(Page(number=i, image=img, rotated=rot, dewarped=warped, notes=notes))
    return pages


# ---------- classify ----------

KEYWORDS = {
    FaxType.cover_sheet: [r"\bfacsimile\b", r"transmittal", r"\bpages?\s*:", r"\bcover\b", r"number of pages",
                          r"\bre\s*:", r"\bmessage\b"],
    FaxType.refill_authorization: [r"refill authori[sz]ation", r"refill request", r"prescriber response",
                                   r"\bapproved\b", r"\bdenied\b", r"last filled"],
    FaxType.other: [r"laboratory", r"\bresults?\b", r"reference range", r"prior authori[sz]ation", r"\bspecimen\b",
                    r"\bcollected\b", r"\bdear\b", r"\bsincerely\b", r"\bflag\b"],
    FaxType.new_rx: [r"\brx\b", r"\bsig\b", r"\bdisp\b", r"\bqty\b", r"\brefills?\b", r"\btablets?\b", r"\btabs?\b",
                     r"\bcaps?(ule)?s?\b", r"\bmg\b", r"\bnpi\b", r"\bpo\b", r"\bdaily\b", r"dispense as written"],
}


def classify(ocr_text: str) -> tuple[FaxType, dict[str, int]]:
    """Keyword vote on the page's OCR text. Ties and empty pages -> new_rx (never drop silently)."""
    t = ocr_text.lower()
    votes = {k.value: sum(1 for p in pats if re.search(p, t)) for k, pats in KEYWORDS.items()}
    if votes[FaxType.refill_authorization.value] >= 2:
        return FaxType.refill_authorization, votes
    best = max(votes, key=votes.get)
    if votes[best] == 0 or (best != FaxType.new_rx.value and votes[best] <= votes[FaxType.new_rx.value]):
        return FaxType.new_rx, votes
    return FaxType(best), votes


if __name__ == "__main__":
    import sys

    from backend.extract import ocr
    for p in sys.argv[1:]:
        for page in prepare(Path(p)):
            text, _, _ = ocr.read(page.image)
            kind, votes = classify(text)
            print(f"{p} page {page.number}: {kind.value} votes={votes} rotated={page.rotated} "
                  f"dewarped={page.dewarped} size={page.image.size}")
