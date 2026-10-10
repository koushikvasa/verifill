# Frontend → Backend handover

The pharmacist console is in `frontend/index.html` (plus `frontend/demo-faxes.js`, which holds the demo images). There's no build step and no npm. It loads three.js r128 from a CDN.

## Run it

```bash
uvicorn backend.app:app --reload --port 8000       # backend (CORS is already open)
python -m http.server 5500 -d frontend             # serve the UI; opening the file directly also works
# open http://localhost:5500, click "Data: Demo" in the top bar → "Live API", URL http://localhost:8000
```

- **Demo** (the default) runs with no backend. It uses built-in orders shaped exactly like `backend/models.py` `Order`, covering RX-001..008 plus an RX-009 injection case.
- **Live API** calls the endpoints listed below. Use Live API for judging so the judges see real output.

## What the UI calls today (Live API mode)

| UI action | Endpoint | Notes |
|---|---|---|
| Header status bar | `GET /health` | reads `ok`, `prism`, `nicrron` |
| Fax inbox | `GET /samples` | uses `id`, `file`, `severity`, `note`, `ground_truth.patient_name/drug/strength` (name/drug only before the fax is read) |
| Thumbnail before reading | `GET /samples/{file}` | |
| Open a fax | `POST /orders/sample/{id}` | **first order only**, see gap 1 |
| Add a fax | `POST /orders/upload` (multipart `file`) | first order only |
| Fax image after reading | `GET /orders/{order_id}/page.png` | the UI switches to this image because `readings[].bbox` refers to it. The crop on each decision card and the 3D boxes use the image's natural size |
| Choose / correct a value | `POST /orders/{id}/resolve` `{field, value, pharmacist:"pharmacist on duty"}` | "Keep as written" sends `override_reason:"Prescriber confirmed as written"`. A 409 shows `failed_checks` in a toast |
| Bottle scan | `POST /orders/{id}/scan` `{ndc}` | expects `{match, detail}` |

### Fields the UI relies on (please keep them stable)

- **`order.status`:** `ready_to_fill` enables "Approve for filling". `rejected` shows the Fax blocked screen. `not_prescription` and `denied_by_prescriber` show a Filed screen (with `callback_draft` if present). Anything else is treated as needing review.
- **`fields[*].status`:** `verified` / `review` / `conflict` / `confirmed`. Every `review` or `conflict` field becomes one decision card, sorted conflict-first, then by critical field.
- **`fields[*].reasons`:** shown word for word as the bullet points. Lines ending in "readers agree" are hidden. **Plain English please; the pharmacist reads these.**
- **`fields[*].candidates`:** shown as big option buttons (drug look-alikes, suggested NPI, suggested qty). For `strength` the button reads "Change to X / only after the prescriber confirms".
- **`fields[*].checks[*]`:**
  - a failed check's `detail` becomes a bullet point;
  - `grounded_in` is shown as a source tag: `prism_kb`→PRISM KB, `rxnav`→RxNorm, `nppes`→NPPES, `local`→Rule.
- **`fields[*].readings[*].source`:** the prefixes `ocr`, `reader_a`, `reader_b` (and `zoom`) are shown as OCR / AI reader A / AI reader B. `readings[0].bbox` (the first one that has a bbox) drives the crop and the 3D box. **If no reading has a bbox, the UI falls back to fixed demo positions, which will be wrong for real faxes.**
- **`pick_ticket`:**
  - `blocked` shows the stock-locked note with `warnings`;
  - otherwise `item` (or `substitution`) gives Aisle / Shelf / Bin, NDC, lot, expiry and `qty_to_pick`/`qty_needed`/`owed`;
  - `substitution_requires_approval` shows an "Approve substitution" button (frontend only, see gap 4).
- **`callback_draft`:** shown in "Call the prescriber" with a copy button. The UI pulls the phone number from a `Tel (xxx) xxx-xxxx` line if present.
- **`audit[*]`:** `at`, `actor`, `action`, `detail` and `trace_id` are shown in the Activity log, with "traced in PRISM".

## Gaps the backend could close (in priority order)

1. **Multi-order faxes.** The UI uses `/orders/sample/{id}`, which returns the first order only. Switching to `/faxes/sample/{id}` (a list) needs a small UI change: one queue row per order, grouped by `fax_id`. That's about 20 lines in `openRx`/`renderQueue`, and the frontend owner can do it if the backend confirms the list order is stable.
2. **Guardrail position.** For `rejected` orders, the UI boxes the injected line with a hard-coded demo bbox. Please add something like `guardrail_bbox: [x0,y0,x1,y1]` on `Order` (or put it in `audit.detail`) so the box lands on the real line.
3. **Release a blocked fax.** Demo mode lets a pharmacist release a guardrail-blocked fax after ticking "verified with the sending office", and undo that with "Block again". Live mode disables the button because there's no endpoint. Suggested:
   - `POST /orders/{id}/release` `{pharmacist, reason}` → re-run reading with the injected text still excluded from prompts. Keep `guardrail_flags` in an `override` record. Force `patient_name` and `npi` to `review` so they must be confirmed. Never return `ready_to_fill` directly.
   - `POST /orders/{id}/block` → restore the rejected order. Add an audit event for both.
4. **Reject / undo reject, approve, mark filled, approve substitution.** These are frontend-only state right now (they're written into the UI's copy of `audit`, not the server's). Suggested endpoints:
   - `POST /orders/{id}/reject {pharmacist, reason}` and `POST /orders/{id}/unreject`
   - `POST /orders/{id}/approve` (refuse unless `ready_to_fill`)
   - `POST /orders/{id}/filled {pharmacist, ndc, qty}` (refuse unless the last scan matched)
   - `POST /orders/{id}/substitution/approve`

   Each one only needs to append an `AuditEvent` and set a status (`rejected_by_pharmacist`, `approved`, `filled`). The UI will read whatever `status` comes back.
5. **Pharmacist name.** The UI sends `pharmacist:"pharmacist on duty"`. If you want real names in the audit, add a `GET /me` or accept a header, and the UI will pass it through.

## Behaviour the UI enforces (keep the backend matching)

- "Approve for filling" is disabled unless `order.status === "ready_to_fill"`. The UI never decides this itself in Live mode.
- "Mark as filled" is disabled until `/scan` returns `match: true`.
- Stock stays locked while the drug isn't verified (the UI just shows what `pick_ticket.status === "blocked"` says).
- A released fax can never go straight to ready: the patient and NPI must be confirmed first.

## Files

- `frontend/index.html`: the whole UI. Its CSS tokens (light and dark) are at the top of `<style>`. JS sections, in order: demo data → state/API → queue → decision cards → fill → 3D scene (three.js).
- `frontend/demo-faxes.js`: base64 demo images, used in Demo mode only.
- `frontend/mock/`: the backend's captured responses. The UI follows those shapes.
- `frontend/NINOO_BUILDER_PROMPT.md`: the original spec. This UI departs from it in layout (queue → decisions → fill) and colours (light/dark pharmacy theme), but keeps every rule in it.
