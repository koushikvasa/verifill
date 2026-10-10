# Handling any kind of fax

A pharmacy fax inbox isn't just Rx pads. Verifill must handle every page it receives, and the correct answer is sometimes "this isn't a prescription" or "no order".

## New stage 0: ingest (runs before the guardrail)

```
file (png/jpg/pdf, any number of pages)
  └─► split pages (PDF → images at 200 dpi, pypdfium2 or pdf2image)
       └─► per page: fix orientation → dewarp/deskew → classify page
            ├─ cover_sheet          → skip (audit: "page 1 skipped: cover sheet")
            ├─ other (labs, letters)→ not_prescription, filed, no order
            ├─ refill_authorization → read prescriber's answer
            │     ├─ APPROVED → renewal order (refills = handwritten number)
            │     └─ DENIED   → denied_by_prescriber + reason, no order
            └─ new_rx               → extract → may contain N drugs → N orders (shared fax_id)
```

Each order then goes through the existing pipeline (guard → readers → verify → score → inventory).

## How each case is handled

| Fax kind | Sample | Technique | Correct output |
|---|---|---|---|
| Handwritten pad | RX-001..008 | readers + OCR vote | 1 order |
| Prompt injection | RX-009 | guard.py on OCR text of **every page** before any model call | `rejected` |
| Typed EHR printout | RX-010 | same readers; OCR confidence will be high | 1 order, mostly green |
| Multiple drugs on one page | RX-011 | reader returns `medications: [...]`; one Order per item, same `fax_id` | 3 orders |
| Cover sheet + Rx (PDF) | RX-012 | page classifier → skip cover | 1 order from page 2 |
| Upside down / sideways | RX-013 | `pytesseract.image_to_osd(img)` → `Rotate: 180` → rotate before anything else; record `orientation_fixed` | 1 order |
| No printed labels | RX-014 | vision readers are layout-agnostic; OCR can't parse labels, so it votes by **finding each reader's value in the OCR text** (rapidfuzz partial_ratio) instead of label parsing | 1 order |
| Refill request approved | RX-015 | classifier → `refill_authorization`; drug info is pharmacy-printed (high trust), prescriber answer + refills are handwritten (score these strictly) | renewal order |
| Refill request denied | RX-016 | classifier reads which box is checked + reason | `denied_by_prescriber`, reason shown |
| Phone photo | RX-017 | detect page contour (OpenCV largest 4-point contour) → perspective warp → adaptive threshold | 1 order |
| Not a prescription | RX-018 | classifier → `other` | `not_prescription`, no order |
| Schedule II by fax | RX-019 | KB says schedule CII → rule check fails | `needs_pharmacist`, never auto-fill |

## Page classifier (cheap and explainable)

1. **Keyword vote on OCR text** (fast, deterministic):
   - cover: "facsimile", "transmittal", "pages:", "cover"
   - refill: "refill authorization", "refill request", "approved", "denied", "prescriber response"
   - other: "laboratory", "results", "reference", "prior authorization", "letter"
   - new_rx: "rx", "sig", "disp", "qty", "refills", a drug name from the KB
2. **Reader A confirms** with a one-word answer (`new_rx | refill_authorization | cover_sheet | other`).
3. Disagreement → treat as `new_rx` and let the pharmacist see it (never silently drop a page that might be a prescription).

Trace the classifier as stage `classify`.

## Reader output change (multi-drug)

Ask the readers for:
```json
{"page_type": "...", "patient_name": {...}, "dob": {...}, "date_written": {...},
 "prescriber_name": {...}, "npi": {...},
 "refill_response": {"value": "approved|denied|null", "legible": true},
 "medications": [ {"drug": {...}, "strength": {...}, "dosage_form": {...}, "sig": {...},
                   "quantity": {...}, "days_supply": {...}, "refills": {...}, "daw": {...}} ]}
```
Patient and prescriber fields are shared; each medication becomes its own Order. Match readers' medication lists by drug name (rapidfuzz). A drug that only one reader sees → its own order, flagged "only one reader found this medication".

## Image prep (OpenCV, ~30 lines)
- orientation: Tesseract OSD (skip if it errors on low-text pages)
- photo dewarp: grayscale → blur → Canny → largest 4-point contour covering >40% of the image → `warpPerspective`
- deskew: `minAreaRect` angle on text pixels, rotate if |angle| > 0.5°
- binarize: `adaptiveThreshold` for photos and scans; leave true 1-bit faxes as they are
- send readers the **cleaned** image plus OCR; log every transform as an audit event

## Ground truth
`samples/faxes/ground_truth.json` now has 19 faxes. Every entry has `kind`, `pages`, `orders` (list), `expected_outcome` (`ready_to_fill | needs_pharmacist | rejected | not_prescription | denied_by_prescriber`) and `expected_flags`. `ground_truth` is still the first order, for older code.
