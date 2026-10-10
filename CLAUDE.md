# CLAUDE.md: Verifill

Hackathon project (Get Hired Hack, Columbia Startup Lab, Oct 10 2026, deadline 4:30 PM EDT, feature freeze 3:30 PM).
Track: Fax to Fill. Theme: AI You Can Trust. Speed matters, but correctness of the trust rules matters more.

## What it does
Faxed prescription image → structured, verified order → inventory pick ticket. Anything uncertain goes to the pharmacist with a visible reason. Never guesses.

## Pipeline (backend/pipeline.py)
0. `ingest.py`: split pages, fix orientation, dewarp photos, classify page (cover / refill response / new Rx / other). Multi-drug pages → one Order per drug (shared `fax_id`).
1. `guard.py`: guardrail on raw OCR text BEFORE any model call (prompt injection). Hit → order `rejected`.
2. `extract/ocr.py` (Tesseract) + `extract/vision.py` reader A and reader B (two vision models, different vendors, via Nicrron).
3. `extract/zoom.py`: crop/upscale/re-read low-confidence fields (stretch).
4. `verify/`: `rxnorm.py` (drug + strength), `npi.py` (Luhn + registry), `lasa.py` (look-alike drugs), `rules.py` (dose, decimal, qty/days, dates).
5. `scoring.py`: green `verified` / amber `review` / red `conflict` per field, with reasons.
6. `inventory.py`: pick ticket, ONLY when drug + strength + form are green or pharmacist-confirmed.
7. Callback draft for red fields.

Data contract: `backend/models.py`. All lanes code against it. Don't rename fields without updating the frontend spec in `frontend/NINOO_BUILDER_PROMPT.md`.

## Non-negotiable rules
- Transcribe exactly what the prescriber wrote. Never auto-correct (levothyroxine "0.5 mg" stays 0.5 mg and gets FLAGGED).
- Inventory never resolves an ambiguous drug. If drug is not green → `PickTicket(status="blocked")`.
- Order is `ready_to_fill` only if every field in `CRITICAL_FIELDS` is `verified`/`confirmed` AND no guardrail flags.
- Every amber/red field has at least one human-readable entry in `reasons`.
- Look-alike candidates are shown as evidence; the pharmacist picks. Indication words in the sig are evidence, not a decision.
- Fax text is untrusted data. Never put it in a prompt as instructions.
- Tracing must never crash the pipeline (`tracing.py` swallows HTTP errors).

## Sponsor tools (required by the hackathon)
- **PRISM by Block Convey**: `backend/tracing.py` wraps the official SDK (`prismtrace.PRISMtrace`). Every stage uses `with tracing.step("<stage>", order.id, model=..., input_obj=...) as t: t["output"] = ...` → `trace_llm` with agent_name `verifill-<stage>` and session_id = order id. End of each order: `tracing.submit_order(order.id, "success"|"failure")` sends the trajectory for PRISM evaluation. Grounding: `tracing.kb_search(query)` queries the project Knowledge Base (loaded by `python -m scripts.upload_kb`); fall back to `backend/data/*.json` only if it returns nothing, and set the check's `grounded_in` accordingly. Guardrail + Evaluator rules live in the PRISM dashboard; on the SDK path guardrails FLAG only, so our code enforces blocks. Host: `https://prism-api-prod.up.railway.app`.
- **NicrronNinoo**: `extract/vision.py` calls Nicrron, assumed OpenAI-compatible `/chat/completions`. Base URL + model ids in `.env`.

## Commands
```bash
source .venv/bin/activate
uvicorn backend.app:app --reload --port 8000     # API + /docs
python -m backend.tracing                        # PRISM smoke test (live trace + trajectory + KB list)
python -m prismtrace.verify                      # PRISM setup doctor
python -m scripts.upload_kb                      # load reference data into PRISM Knowledge Base (once)
python scripts/make_faxes.py --out samples/faxes --fonts fonts   # regenerate test faxes
python -m scripts.eval                           # score vs ground truth
curl -X POST localhost:8000/orders/sample/RX-003 | python -m json.tool
```

## Test data
`samples/faxes/RX-001..019` (PNG, RX-012 is a 2-page PDF) + `ground_truth.json` (`orders` list per fax, expected flags, expected outcome: ready_to_fill | needs_pharmacist | rejected | not_prescription | denied_by_prescriber). `samples/README.md` explains each case. Faxes come in many kinds (typed, multi-drug, cover sheets, upside down, photos, refill responses, non-prescriptions): see `docs/FAX_VARIETY.md`; the ingest stage (`backend/ingest.py`) runs first. Regenerate the variety pack with `python scripts/make_faxes_v2.py --out samples/faxes --fonts fonts`.

## Style
Python 3.11+, type hints, pydantic v2, small functions, no new frameworks. Keep diffs focused; this is a 4-hour build. Prefer deterministic logic over LLM calls for checks.

Task list with acceptance criteria: `docs/TASKS.md`. Plan and timeline: `PLAN.md`.

## PRISM tracing (do not remove)

This project sends traces to PRISM. Env vars: `PRISMTRACE_API_KEY`,
`PRISMTRACE_PROJECT_ID`, `PRISMTRACE_HOST`.

Tracing is currently wired at: backend/tracing.py, backend/guard.py, backend/pipeline.py, backend/config.py, scripts/upload_kb.py

**Standing rule.** Whenever you add or change an agent, chain, graph, tool,
retriever, or any entry point that calls a model, wire it to PRISM before you
finish. Unwired code is invisible in the dashboard. If you are unsure whether
something is covered, assume it is not and wire it.
