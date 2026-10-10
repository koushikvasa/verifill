# Verifill

**Verifill turns faxed prescriptions into verified, ready-to-fill pharmacy orders, finds the bottle on the shelf, and sends anything it isn't sure about to the pharmacist with a reason, never a guess.**

Built in one day at Get Hired Hack, Columbia Startup Lab (Oct 10, 2026) · Track: Fax to Fill · Theme: AI You Can Trust

Sponsor tools: **NicrronNinoo** (the two vision readers) and **PRISM by Block Convey** (tracing of every stage, trajectory evaluation, Knowledge Base grounding).

## Results (reproducible: `python -m scripts.eval`, `python -m scripts.robustness`)

| Metric | Value |
|---|---|
| Auto-accept precision (green fields that equal ground truth) | **100%** (175 / 175) |
| Coverage (fields auto-accepted) | 71% |
| Expected safety flags raised | 81% |
| Faxes ending in the expected state | 18 / 19 |
| **False "ready to fill"** | **0** |
| Adversarial kit (injections, noise, blank, resend, thumbnail, sideways, TIFF…) | 12 / 12, no crashes |

The missing 29% coverage is by design: an unnecessary amber costs a pharmacist thirty seconds; a wrong green is a dispensing error.

## How it works

```
fax (png/jpg/pdf/tiff) -> ingest: pages, orientation (OSD), photo dewarp
  -> Tesseract OCR on every page -> injection guardrail BEFORE any model call
  -> reader A (Claude) + reader B (GPT) via Nicrron, concurrently, + RapidOCR grounding text
  -> page classifier (keywords + both readers; disagreement => treat as a prescription)
  -> one Order per medication -> verify -> zoom-and-reread unclear fields -> score
  -> inventory (only if drug/strength/form are green) -> safety gate -> callback draft
```

Rules that never bend (enforced in code and re-asserted by a final safety gate):
- **Transcribe exactly.** Levothyroxine "0.5 mg" stays 0.5 mg and gets flagged as 10× the marketed 0.05 mg. A partly legible name comes back as `Hy????zine`, never completed.
- **Green needs two independent readers agreeing, exact match on numbers, and the value physically found on the page by OCR.** Every check records its source (`prism_kb`, `rxnav`, `nppes`, `local`).
- **Look-alike drugs are shown as candidates with evidence; the pharmacist picks.** Inventory never resolves an ambiguous drug.
- **An order is `ready_to_fill` only when every critical field is verified or pharmacist-confirmed and no guardrail fired.**
- **Fax text is data, not instructions.** The guard runs on OCR text and on reader outputs.
- **Pharmacist corrections are re-verified.** Confirming a value that still fails a check needs a typed reason; everything is in the audit trail.

## Run it locally

Requirements: Python 3.11+, Tesseract 5, ~1 GB disk (RapidOCR models). Keys for Nicrron and PRISM (free hackathon credits; the app degrades safely without them: readers go amber, tracing no-ops).

```bash
git clone https://github.com/koushikvasa/verifill && cd verifill
python -m venv .venv
source .venv/bin/activate                 # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                      # fill NICRRON_* and PRISMTRACE_*; see notes below
uvicorn backend.app:app --reload --port 8000
```

Tesseract: macOS `brew install tesseract`, Debian/Ubuntu `apt install tesseract-ocr tesseract-ocr-osd`, Windows the [UB-Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) (auto-detected at `C:\Program Files\Tesseract-OCR`, or set `TESSERACT_CMD` in `.env`).

Then open http://localhost:8000/docs, or the pharmacist console:

```bash
python -m http.server 5500 -d frontend    # http://localhost:5500 -> top bar "Data: Demo" -> "Live API"
```

Or build the single container that serves both the API and the UI at one URL (Railway/Render/any Docker host):

```bash
docker build -t verifill . && docker run -p 8000:8000 --env-file .env verifill   # UI at http://localhost:8000/
```

### Try it

```bash
curl -X POST localhost:8000/faxes/sample/RX-001 | python -m json.tool     # all green, pick ticket Aisle 3 · B · 12
curl -X POST localhost:8000/faxes/sample/RX-003 | python -m json.tool     # look-alike: candidates, inventory locked
curl -X POST localhost:8000/faxes/sample/RX-004 | python -m json.tool     # 0.5 mg transcribed, flagged, callback draft
curl -X POST localhost:8000/faxes/sample/RX-009 | python -m json.tool     # prompt injection -> rejected
curl -X POST localhost:8000/faxes/sample/RX-011 | python -m json.tool     # three drugs -> three orders
curl -F file=@samples/faxes/RX-017_fax.png localhost:8000/faxes/upload    # your own fax or phone photo
```

### Verify it

```bash
python -m pytest tests -q                 # 45 offline tests of the trust invariants, ~2 s
python -m scripts.demo_check              # end-to-end against the running server, ~4 min
python -m scripts.eval                    # 19 faxes vs ground truth -> the table above, ~10 min
python -m scripts.robustness              # the 12-file adversarial kit
python -m backend.tracing                 # PRISM smoke test (trace + trajectory + KB listing)
python -m scripts.upload_kb               # load backend/data into the PRISM Knowledge Base (once per project)
```

### `.env` notes
- `VERIFILL_OFFLINE=0` also queries RxNav and NPPES live for drugs and NPIs outside `backend/data` (fails safe to amber on timeout). `1` keeps everything local and deterministic.
- `GROUND_OCR=rapidocr` is the second OCR engine that confirms reader values on the page. `tesseract` works with nothing extra installed, but confirms fewer handwritten values.
- Windows with HTTPS-inspecting antivirus (e.g. Norton): the app injects `truststore`; pip may need `--use-feature=truststore`. Set `PYTHONIOENCODING=utf-8` for scripts that print `✓`.

## API

| Endpoint | Purpose |
|---|---|
| `POST /faxes/sample/{id}`, `POST /faxes/upload` | run a fax; returns a **list** of orders (one per medication) |
| `POST /orders/sample/{id}`, `POST /orders/upload` | same, first order only |
| `GET /orders/{id}`, `GET /orders/{id}/page.png` | the order; the cleaned page the readers saw (bboxes refer to it) |
| `POST /orders/{id}/resolve` `{field, value, pharmacist, override_reason?}` | pharmacist confirms/corrects; 409 with `failed_checks` if it still fails |
| `POST /orders/{id}/scan` `{ndc}` | bottle scan against the pick ticket |
| `POST /orders/clear` | forget everything in memory |
| `GET /health`, `GET /samples` | status; sample faxes with ground truth |

Data contract: `backend/models.py`. Orders live in memory; uploads are deleted after processing; PRISM traces carry text fields only, never images.

## Test data

`samples/faxes/RX-001..019` with `ground_truth.json` (`samples/README.md` explains each case): handwritten pads in mild-to-unreadable condition, a typed EHR printout, three drugs on one page, a cover sheet + Rx PDF, an upside-down page, a freeform note, refill responses (approved and denied), a phone photo, a lab result, a Schedule II opioid, and a fax with hidden injected instructions. `samples/robustness/` holds the 12-file adversarial kit and a printable blank pad for live testing. All patients, prescribers and NPIs are fictional. Regenerate with `scripts/make_faxes.py`, `make_faxes_v2.py`, `make_robustness.py` (fonts in `fonts/`).

## Repository

```
backend/        pipeline.py (orchestrator) · models.py (contract) · scoring.py · safety.py · inventory.py · callback.py · app.py
backend/extract ocr.py (Tesseract) · vision.py (Nicrron readers) · ground_ocr.py (RapidOCR) · zoom.py
backend/verify  rxnorm.py · npi.py · lasa.py · rules.py · sig.py · dea.py
backend/data    drugs, look-alike pairs, prescriber registry, inventory (the PRISM KB holds the same)
frontend/       index.html pharmacist console (no build) · mock/ real API responses
scripts/        eval, robustness, demo_check, upload_kb, fax generators
tests/          trust invariants, inventory paths, hardening, DEA/zoom
docs/           HANDOVER.md · DEVPOST.md · DEMO_SCRIPT.md · FAX_VARIETY.md · TASKS.md
```

## Team

| Name | Role |
|---|---|
| Koushik Vasa | Backend / pipeline, verification, deployment |
| Akash Devulapally | Pharmacist console (frontend) |
