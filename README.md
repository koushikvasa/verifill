# Verifill

**Verifill turns blurry prescription faxes into verified, ready-to-fill orders, finds the bottle on the shelf, and sends anything it isn't sure about to the pharmacist instead of guessing.**

Built at Get Hired Hack @ Columbia Startup Lab (Oct 10, 2026) · Track: Fax to Fill · Theme: AI You Can Trust

## How it handles uncertainty
- **Two independent readers** (vision models from different vendors via Nicrron) plus Tesseract. Disagreement = uncertainty.
- **Zoom-and-reread** on low-confidence fields; still unsure → flagged, never guessed.
- **Grounded checks** via PRISM Knowledge Base, RxNorm and NPPES: drug/strength exists, NPI is valid and matches, dose under the daily max, no 10× decimal errors, quantity matches days' supply, look-alike drug guard (ISMP).
- **Every field is green / amber / red with a visible reason.** Only all-green orders can be filled.
- **Inventory only runs after the drug is verified.** Stock never decides an ambiguous drug.
- **Guardrail** on fax text before any model sees it (faxes are untrusted input).
- **Full audit trail** traced in PRISM.

## Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # tesseract binary: mac `brew install tesseract`; Windows: UB-Mannheim installer + TESSERACT_CMD in .env
cp .env.example .env                     # fill in Nicrron + PRISM keys
python -m backend.tracing                # PRISM handshake
uvicorn backend.app:app --reload         # http://localhost:8000/docs
```

## Test data
`samples/faxes/`: 8 synthetic prescriber-to-pharmacy faxes, mild to unreadable, with `ground_truth.json` and expected flags. All patients, prescribers and NPIs are fictional. Regenerate with `python scripts/make_faxes.py --out samples/faxes --fonts fonts`. See `samples/README.md`.

## Tech
NicrronNinoo (Nicrron, Ninoo Builder, Ninoo Code) · PRISM by Block Convey (tracing, Guardrails, Evaluators, Knowledge Base) · FastAPI · Tesseract · OpenCV · RxNorm · NPPES · rapidfuzz

## Team
| Name | Role |
|---|---|
| Koushik Vasa | Backend / pipeline |
| | |
