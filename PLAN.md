# Verifill: build plan

**One-liner:** Verifill turns blurry prescription faxes into verified, ready-to-fill orders, finds the bottle on the shelf, and sends anything it isn't sure about to the pharmacist instead of guessing.

**Track:** 💊 Fax to Fill · **Deadline:** 4:30 PM EDT · **Feature freeze:** 3:30 PM

## Pipeline
```
fax ─► PRISM Guardrail ─► Reader A + Reader B (Nicrron, 2 vendors) + Tesseract
    ─► zoom-and-reread (low-confidence fields only)
    ─► verify: PRISM Knowledge Base / RxNorm / NPPES · look-alike guard · dose & qty rules
    ─► score each field green / amber / red (+ reasons) ─► PRISM Evaluator on verdicts
    ─► inventory + pick ticket (ONLY if drug verified) ─► pharmacist review UI
every step traced in PRISM (agent_name = verifill-<step>, session_id = order id)
```

## Lanes and files
| Lane | Owner | Files |
|---|---|---|
| Backend | Koushik | `backend/pipeline.py`, `extract/*`, `verify/*`, `scoring.py`, `guard.py`, `app.py` |
| Data + eval | ___ | `backend/data/*`, `scripts/make_faxes.py` (add RX-009 injection fax), `scripts/eval.py`, `inventory.py` logic |
| Frontend | ___ | `frontend/` (generate with Ninoo Builder from `frontend/NINOO_BUILDER_PROMPT.md`) |
| Sponsors + pitch | ___ | PRISM dashboard (guardrail, evaluator, KB uploads), Devpost, demo video |

Contract between lanes = `backend/models.py`. Don't change it without telling the team.

## Timeline
| Time | Backend | Data + eval | Frontend | Sponsors + pitch |
|---|---|---|---|---|
| 12:05–12:30 | env up, `/health` green | RX-009 injection fax | Ninoo Builder → UI shell on mock JSON | PRISM workspace + coupon, invite, keys; Nicrron credits |
| 12:30–1:30 | OCR parse + reader A/B + scoring v1 | inventory logic + FEFO | wire `/samples`, `/orders/sample` | upload drugs/LASA/prescribers/inventory to PRISM KB; create guardrail rules |
| 1:30–2:30 | verify: rules, LASA, NPI, KB queries | eval.py v1 | field rows, why-panel, pick ticket | evaluator rubric on `verifill-score` |
| 2:30–3:15 | resolve + scan endpoints, zoom (stretch) | run eval, fix | resolve/approve buttons, audit drawer | Devpost draft, video script |
| 3:15–3:30 | **integration on all 9 faxes** | numbers final | polish | screenshots |
| 3:30 | **FEATURE FREEZE** | | | |
| 3:30–4:30 | bugfix + rehearse | | | record video, submit by 4:15 |

## PRISM checklist (all 4 rules)
- [ ] One workspace, project named after the team, coupon `PROOFOFWORK`, teammates invited
- [ ] `agent_name` on every trace (handled in `backend/tracing.py`)
- [ ] **Guardrail** on input: Prompt Injection + PII/PHI rules on `verifill-guard` (dashboard). SDK path flags only, so `guard.py` enforces the block
- [ ] **Evaluator** on output: rubric on `verifill-score` verdicts ("every green field has 2 agreeing readers + passing grounded checks")
- [ ] **Knowledge Base**: upload `drugs.json`, `lasa_pairs.json`, `prescribers.json`, `inventory.json`. ⚠️ Docs don't show a KB query API: ask on Discord how to query the KB from code
- [ ] `python -m backend.tracing` handshake passes

## Nicrron checklist
- [ ] Everyone redeems `COLUMBIA26`
- [ ] From the guide: base URL, key, and two vision model ids from different vendors → `.env`
- [ ] Confirm the endpoint is OpenAI-compatible (`extract/vision.py` assumes it); adjust if not

## Demo script (90 s)
1. RX-001 clean → all green → "Aisle 3 · Shelf B · Bin 12" → ready to fill
2. RX-003 smudged "Hy▓▓zine" → red, both candidates, "PRN itching" as evidence, inventory locked → pharmacist picks
3. RX-004 levothyroxine 0.5 mg → read correctly AND caught as a 10× error (grounded in KB) → callback draft
4. RX-009 injection fax → guardrail blocks it live
5. RX-008 worst case → refuses to auto-fill
6. Eval slide: auto-accept precision, flag recall, **0 false "ready to fill"**

## Rules we don't break
- Never "correct" what the prescriber wrote. Transcribe, then flag.
- Inventory never resolves an ambiguous drug.
- No order is ready_to_fill unless every critical field is green.
- Every color has a visible reason.
