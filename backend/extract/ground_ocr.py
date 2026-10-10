"""
Lane: Backend
Grounding OCR: a second, non-LLM engine used ONLY to confirm that what the vision readers
read is actually on the page (scoring._grounded). Tesseract stays for the injection guard,
orientation and page classification (printed text, fast, must run before any model call).

GROUND_OCR=rapidocr (default) | easyocr | tesseract
Bench (19 faxes, strict grounding): Tesseract+RapidOCR confirms 79% of true values and 2/114
dangerous near-misses; Tesseract alone 61%. EasyOCR: no better than Tesseract, ~38 s/page.
Runs in parallel with the vision readers. Any failure returns "" -> scoring falls back to
the Tesseract text, so a missing engine can only make fields amber, never green.
"""
import threading

import cv2
import numpy as np
from PIL import Image

from backend import config
from backend.extract import ocr

_lock = threading.Lock()       # one model instance, shared across request threads
_engine = None


def _load():
    global _engine
    if _engine is None:
        if config.GROUND_OCR == "rapidocr":
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
        elif config.GROUND_OCR == "easyocr":
            import easyocr
            _engine = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _engine


def text(img: Image.Image) -> str:
    """Page text from the grounding engine ('' if disabled or failed)."""
    if config.GROUND_OCR == "tesseract":
        return ""
    try:
        clean, _ = ocr.preprocess(img)
        a = np.array(clean)
        with _lock:
            eng = _load()
            if config.GROUND_OCR == "rapidocr":
                res, _ = eng(cv2.cvtColor(a, cv2.COLOR_GRAY2BGR))
                lines = [t for _, t, _ in (res or [])]
            else:
                lines = [t for _, t, _ in eng.readtext(a)]
        return "\n".join(lines)
    except Exception:  # noqa: BLE001  fail closed: no grounding text -> fields stay amber
        return ""


def warm() -> None:
    """Load the model at startup so the first fax isn't slow."""
    if config.GROUND_OCR != "tesseract":
        with _lock:
            _load()
