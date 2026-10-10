# Verifill test faxes

8 synthetic prescriber-to-pharmacy faxes in bad condition, each with ground truth and the flags a careful pharmacist should raise. Everything is fictional: patients, prescribers, phone numbers, NPIs.

| ID | Severity | What's wrong with it | Should Verifill... |
|---|---|---|---|
| RX-001 | mild | Light skew/speckle | Go all green → pick ticket |
| RX-002 | bad | Blur, 204×98 standard-mode squash, scanner streaks, dropout | Flag low-confidence handwriting |
| RX-003 | bad | Smudge over the drug name ("Hy▓▓zine"), re-scanned printout | Fire look-alike guard (hydroxyzine vs hydralazine); NOT let inventory pick |
| RX-004 | bad | Fax-of-a-fax; prescriber wrote levothyroxine **0.5 mg** | Catch probable 10× decimal error, request callback |
| RX-005 | bad | Transmission dropout rows shear through quantity; DAW checked | Flag qty; block generic substitution |
| RX-006 | very bad | Messy script, clonidine (looks like clonazepam), NPI digits transposed | Fire look-alike guard + fail NPI check digit |
| RX-007 | bad | Toner fade on right, re-scanned; qty 90 for 1/day × 30 days | Flag qty/days mismatch |
| RX-008 | extreme | Everything: blur, double fax, streaks, crop, RECEIVED stamp | Refuse to auto-fill; route whole order to pharmacist |

### Variety pack (scripts/make_faxes_v2.py)
| ID | Kind | Should Verifill... |
|---|---|---|
| RX-009 | Rx pad with a prompt-injection line in the footer | `rejected` by guardrail before any model call |
| RX-010 | Typed EHR printout, 1 drug | Go green → `ready_to_fill` |
| RX-011 | Typed EHR printout, **3 drugs** | Split into 3 orders |
| RX-012 | **2-page PDF**: cover sheet + Rx | Skip cover, 1 order |
| RX-013 | Rx pad **upside down** | Auto-rotate, `ready_to_fill` |
| RX-014 | Freeform notepad, **no printed labels** | Read layout-agnostically |
| RX-015 | Refill request faxed back **APPROVED** (3 refills) | Renewal order |
| RX-016 | Refill request faxed back **DENIED** | `denied_by_prescriber`, show reason |
| RX-017 | **Phone photo** of a fax on a desk | Dewarp, then read |
| RX-018 | **Lab results**, not an Rx | `not_prescription`, no order |
| RX-019 | **Schedule II** (oxycodone) by fax | `needs_pharmacist`, never auto-fill |

How the pipeline handles each kind: `docs/FAX_VARIETY.md`.

## Files
- `faxes/RX-00N_fax.png`: the degraded fax (input to your pipeline)
- `faxes/RX-00N_clean.png`: the same Rx before damage (for debugging only)
- `faxes/fax_inbox_all.pdf`: all 8 as one multi-page "fax inbox" PDF
- `faxes/ground_truth.json`: what was actually written, expected flags, expected outcome
- `faxes/contact_sheet.png`: thumbnails of all 8
- `../scripts/make_faxes.py`: regenerate or add cases (edit `CASES`)
- `../backend/data/`: demo reference data (drug strengths and daily max, ISMP look-alike pairs, fictional prescriber registry)

## Regenerate
```bash
pip install pillow numpy
cd scripts && python make_faxes.py --out ../samples/faxes --fonts ../fonts
```

Raw Tesseract on RX-006 reads "Clonidine 0.1 mg tablet" as "Cenitane 0 mug tablet", which is exactly why the two-reader + verification design matters.
