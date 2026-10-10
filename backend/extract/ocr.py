"""
Lane: Backend
Reader 0: Tesseract. Non-AI vote with per-word confidence + bounding boxes.

TODO:
- preprocess(img): deskew (cv2.minAreaRect on text pixels), denoise (median 3), Otsu threshold
- read(img) -> raw text + list of words {text, conf 0..1, bbox}
- parse_fields(words) -> dict[FieldName, Reading] using the printed labels on the Rx pad
  ("Patient Name", "DOB", "Date", "Sig:", "Qty / Disp #", "Days supply", "Refills", "NPI:")
  field value = handwriting to the right of / on the label's line; conf = mean word conf
"""
from PIL import Image
import pytesseract


def read_words(img: Image.Image) -> list[dict]:
    d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    out = []
    for i, w in enumerate(d["text"]):
        if w.strip():
            out.append({"text": w, "conf": max(float(d["conf"][i]), 0) / 100,
                        "bbox": [d["left"][i], d["top"][i], d["left"][i] + d["width"][i], d["top"][i] + d["height"][i]],
                        "line": (d["block_num"][i], d["par_num"][i], d["line_num"][i])})
    return out


def parse_fields(words: list[dict]):
    raise NotImplementedError
