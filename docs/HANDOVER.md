# Verifill: handover for the next agent

Read this first, then `CLAUDE.md` (rules), then `docs/TASKS.md` (status). Everything below was true at 15:40 EDT on Oct 10 2026, commit `515aa32` on `main`. Feature freeze was 15:30; after that only bug fixes. Devpost submission deadline 16:30 (team target 16:15).

## State in one paragraph
The backend is complete and verified. 19 sample faxes + a 12-file adversarial kit all end in their expected state with **0 false `ready_to_fill`**; `python -m scripts.eval` reports auto-accept precision 100% (175/175 green fields correct), coverage 71%, flag recall 81%. 45 offline tests pass. A single-file frontend (`frontend/index.html`) exists and its Live mode calls endpoints that exist with matching shapes, but **nobody has yet clicked through Live mode in a browser**. That is the one unverified thing.

## Run it
```powershell
.\.venv\Scripts\Activate.ps1                       # Windows; .venv is Python 3.12
uvicorn backend.app:app --reload --port 8000       # API, /docs
python -m http.server 5500 -d frontend             # UI at http://localhost:5500 -> top bar "Data: Demo" -> "Live API"
python -m pytest tests -q                          # 45 offline tests, ~2 s
python -m scripts.demo_check                       # end-to-end against the running server, ~4 min, prints ok/FAIL per step
python -m scripts.eval                             # 19 faxes vs ground truth, ~10 min, writes samples/faxes/eval_results.json
python -m scripts.robustness                       # 12-file judge kit, ~3 min, non-zero exit on crash/false-ready
```
`.env` is gitignored and already filled (Nicrron, PRISM, `VERIFILL_OFFLINE=0`). `.env.example` documents every key.

## Machine-specific traps (this laptop)
- **Norton intercepts HTTPS.** `backend/config.py` injects `truststore`; any standalone script must `import backend.config` first or TLS fails. pip needs `pip install --use-feature=truststore ...`.
- **Console is cp1252.** Set `PYTHONIOENCODING=utf-8` before scripts that print `✓`.
- **Git Bash heredocs with Python patch code break on quotes/escapes.** Write patch scripts to a file and run them; use raw strings for regex text. `git add` explicit paths.
- Tesseract is auto-detected at `C:\Program Files\Tesseract-OCR`; `TESSERACT_CMD` overrides.

## Architecture (read `backend/pipeline.py` top docstring)
```
file -> ingest (pages, OSD rotation, photo dewarp) -> Tesseract OCR every page
     -> guard (injection patterns) on EVERY page before any model call -> rejected if hit
     -> readers A (claude-sonnet-5) + B (gpt-5.6-terra) via Nicrron, concurrently, + RapidOCR grounding text
     -> classify page (keywords + both readers; disagreement => treat as prescription, never drop)
     -> one Order per medication (shared fax_id); refill approved/denied; cover/other filed
     -> per order: verify -> zoom-and-reread non-green fields -> re-verify -> score -> duplicate check
        -> inventory (only if drug/strength/form green) -> safety gate -> callback draft
```
Key files: `models.py` (contract, don't rename fields), `scoring.py` (green/amber/red rules), `verify/__init__.py` (orchestrates checks), `safety.py` (final invariant gate), `inventory.py`, `callback.py`, `app.py`.

## Invariants the code enforces (never weaken these)
1. Green needs two independent sources agreeing; numeric fields match exactly; the value must be confirmed on the page by OCR text (`scoring._grounded`, strict). Model-only values stay amber.
2. Values are transcribed as written. Partial reads come back with `?`; a `?` in a drug name is always red.
3. `ready_to_fill` only when every `CRITICAL_FIELDS` entry is verified/confirmed and no guardrail flags. `safety.gate()` re-asserts this at the end and downgrades + audits on violation.
4. Inventory is blocked unless drug, strength and form are green/confirmed. It never substitutes a different strength.
5. Every amber/red field has at least one reason. Reason prefixes: `evidence only:` (grey), `unverified:` (caps at amber), `suggestion:` (candidates), else red.
6. `/resolve` requires a pharmacist name; the value is re-verified; a still-failing check returns **409** with `failed_checks` unless `override_reason` is given. A confirmed field re-opens if a later check fails.
7. Fax text is data. Guard runs on OCR text, on RapidOCR text, and on reader outputs; a reader's `suspicious_text` rejects only when the deterministic patterns agree.
8. Any stage exception -> audit event + `needs_pharmacist`, never `ready_to_fill`.

## API (all in `backend/app.py`, CORS open)
| Endpoint | Returns |
|---|---|
| `GET /health` | `{ok, prism, nicrron, offline}` |
| `GET /samples`, `GET /samples/{file}` | ground-truth list; sample image |
| `POST /faxes/sample/{id}`, `POST /faxes/upload` | **list** of Orders (multi-drug faxes give several) |
| `POST /orders/sample/{id}`, `POST /orders/upload` | first Order only (what the UI uses today) |
| `GET /orders/{id}` | Order |
| `GET /orders/{id}/page.png` | the cleaned page the readers saw; `readings[].bbox` refer to THIS image |
| `POST /orders/{id}/resolve` `{field, value, pharmacist, override_reason?}` | Order, or 409 `{detail:{message, failed_checks}}` |
| `POST /orders/{id}/scan` `{ndc}` | `{match, detail}` |
| `POST /orders/clear` | wipes in-memory orders, images, duplicate records |
Uploads: png/jpg/webp/pdf/tif(f) ≤ 25 MB, held in a temp dir only during the run. Orders live in memory (`ORDERS` dict); restarting uvicorn clears them.

## Sponsor wiring
- **PRISM** (`backend/tracing.py`, SDK `prismtrace` 0.5.1): every stage is `tracing.step("<stage>", session_id, ...)` -> agent `verifill-<stage>`; each order ends with `submit_order()` (trajectory on agent `verifill-pipeline`, `final_status` success only for `ready_to_fill`); `tracing.kb_search()` grounds drug/NPI checks (`grounded_in="prism_kb"`). Guardrail/Evaluator rules are dashboard config (Lane D), not code. Stage names: ingest, ocr, ground_ocr, guard, reader_a, reader_b, verify, zoom, score, safety_gate, resolve.
- **Nicrron** (`backend/extract/vision.py`): OpenAI-compatible `/chat/completions`. `claude-sonnet-5` rejects `temperature`; `vision.chat()` retries without it.
- **Standing rule from CLAUDE.md:** anything new that calls a model must be wrapped in `tracing.step` before you finish.

## Frontend (`frontend/`, teammate-built, see `docs/FRONTEND_HANDOVER.md`)
- `index.html` + `demo-faxes.js`; no build. Demo mode needs no backend; Live mode uses the endpoints above.
- Verified by code reading only: endpoints, 409 shape, `pharmacist:"pharmacist on duty"`. **Not verified: actual browser run, 3D panel (three.js r128 from cdnjs).**
- Known gaps (accepted for the demo): UI shows the first order only (avoid RX-011 in Live mode); rejected faxes get a demo-positioned box; reject/approve/release buttons are UI-only state; typed faxes (RX-010/013–017) carry no OCR bboxes so crops fall back to demo positions. Stick to pad faxes RX-001/003/004/009/008 in the script.
- If the UI breaks against the API, fix the API side only if it's a shape mismatch; otherwise it's a frontend bug.

## Demo script (PLAN.md) and what to expect
RX-001 all green + Aisle 3·B·12 → RX-003 look-alike, pharmacist picks hydroxyzine, stock unlocks → RX-004 0.5 mg transcribed and flagged 10× (PRISM KB), callback draft → RX-009 blocked by guardrail → RX-008 refuses to fill → eval numbers. Per-fax latency 5–30 s. `scripts/demo_check.py` asserts all of this.

## If something breaks in the last hour
- Server down: `uvicorn backend.app:app --reload --port 8000`; check `/health`.
- Nicrron errors: readers return `error`, fields go amber, order goes to pharmacist; demo still runs but nothing goes green. Check `.env` key/base URL.
- PRISM unreachable: tracing no-ops silently by design; nothing else is affected.
- Live lookups flaky (RxNav/NPPES): they time out in 4–5 s and fail safe to amber; set `VERIFILL_OFFLINE=1` and restart to disable them (sample faxes unaffected).
- Wrong green on a judge's fax: that is the one unacceptable outcome. Capture the Order JSON (`GET /orders/{id}`), find which rule let it through (`safety.violations`, `scoring.score_field`), fix, add a test in `tests/`.

## Not done, by choice
RX-020..022 prescriber-identity faxes (paths unit-tested, no images); PRISM teammate invites (optional); ~700 MB of unused benchmark packages (torch, easyocr, winrt) still in `.venv`, harmless; `backend/extract/ocr_engines.py` / `scripts/bench_ocr.py` live only in a scratchpad, not the repo.
