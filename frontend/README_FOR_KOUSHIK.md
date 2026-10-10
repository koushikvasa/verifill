# Verifill frontend: handover for Koushik

Hi Koushik, this is the pharmacist console for Verifill (Track 4: Fax to Fill), ready to drop into the repo. It's built against `backend/models.py` and your current `backend/app.py` (as of `main` @ `53a5f2c`).

## What's in this zip

| Path | What it is |
|---|---|
| `frontend/index.html` | **The UI.** One file, no build step, loads three.js r128 from a CDN |
| `frontend/demo-faxes.js` | Demo-mode fax images (base64), loaded by `index.html`. Not used in Live mode |
| `frontend/mock/`, `frontend/NINOO_BUILDER_PROMPT.md` | Unchanged copies of what's already in the repo, included so the folder is complete |
| `docs/FRONTEND_HANDOVER.md` | **Read this.** Every endpoint the UI calls, the fields it relies on, and what the backend still needs |
| `verifill-frontend.patch` | The same files as one git commit, ready to apply |
| `standalone/verifill-console-standalone.html` | Single file with the images built in. Double-click to open; handy for the demo video or as a backup |

## Add it to the repo (pick one)

**A. Apply the patch**, which keeps the commit message:
```bash
git checkout main && git pull
git checkout -b frontend/pharmacist-console
git am verifill-frontend.patch          # if it complains: git am --abort, then git am --ignore-whitespace verifill-frontend.patch
git push -u origin frontend/pharmacist-console     # then open a PR, or merge to main
```

**B. Copy the files.** Copy `frontend/index.html`, `frontend/demo-faxes.js` and `docs/FRONTEND_HANDOVER.md` into the repo, then commit and push. Nothing else in the repo is changed.

## Run it

```bash
uvicorn backend.app:app --reload --port 8000
python -m http.server 5500 -d frontend      # then open http://localhost:5500
```
In the top bar, click **Data: Demo → Live API** and keep `http://localhost:8000`. The status bar at the bottom shows whether the backend, PRISM and Nicrron are connected.

**Demo mode** (the default) needs no backend. It plays RX-001..008 plus an RX-009 injection case using orders built to match the backend's Order shape.

## How the UI works (the pharmacist's flow)

1. **Workflow tabs + fax inbox:** All open → Verification → Ready to fill → Completed, plus Rejected / blocked. Each row shows the patient, the drug, and a plain reason ("1 decision: Drug name is unclear").
2. **Decisions first:** every `review`/`conflict` field becomes a card with:
   - a **crop of the actual fax** at `readings[].bbox`;
   - what Verifill read, the reasons, and failed checks;
   - option buttons from `candidates`, a correction box, and Call prescriber (`callback_draft`).
3. **Approve for filling** is enabled **only** when `status === "ready_to_fill"`.
4. **Fill:** Aisle/Shelf/Bin from `pick_ticket`, a substitution approval when needed, and **scan the NDC** (`/scan`). "Mark as filled" stays disabled until the scan matches.
5. **3D viewer (three.js):**
   - **Original fax:** colour-coded boxes; the camera zooms to the field being decided.
   - **Compare readers:** OCR / reader A / reader B / verdict as stacked layers.
   - **Shelf:** the target bin glows; it shows a lock when stock is blocked.
6. **Safety and undo:**
   - Reject asks for a reason and can be undone (Undo message, an "Undo reject" button, or Ctrl+Z).
   - A guardrail-blocked fax can be released only after ticking "verified with the sending office". It still needs patient and NPI confirmed before it can be filled, and it can be blocked again.
7. Light and dark mode (the button in the top bar). Shortcuts: J/K next/prev, 1–3 choose, Enter approve, Ctrl+Z undo.

## Live mode is wired to the real API

- `/health`, `/samples`, `/samples/{file}`, `/orders/sample/{id}`, `/orders/upload`, `/orders/{id}/page.png` (the bboxes are measured on this image), `/orders/{id}/resolve`, `/orders/{id}/scan`.
- Resolve sends `pharmacist:"pharmacist on duty"`. "Keep as written" sends `override_reason`. A **409** shows `failed_checks` to the user.
- `not_prescription` / `denied_by_prescriber` show a "Filed" screen.

## Backend to-dos, in priority order (details in `docs/FRONTEND_HANDOVER.md`)

1. **Multi-order faxes:** the UI calls `/orders/sample/{id}` (first order only). `/faxes/sample/{id}` needs a small UI change; tell us the list order is stable.
2. **`guardrail_bbox`** on rejected orders, so the injected line is boxed in the right place (today it uses a demo position).
3. **Release / re-block** endpoints for guardrail-blocked faxes. Live mode disables the button until these exist.
4. **Reject / unreject / approve / filled / approve-substitution** endpoints. These are frontend-only state today and aren't in the server audit.
5. Optional: a real pharmacist name for the audit.

## Testing status

- **Done:** every flow clicked through in Demo mode with no JavaScript errors: decisions, approve, scan, fill, reject and undo, release and block again, light/dark, and the filed and blocked screens.
- **Not done (please check before recording):**
  - **Live mode against your running backend.** I couldn't run it from my environment.
  - **The 3D rendering itself.** My test browser couldn't load three.js, so open it once in Chrome and click the three viewer tabs.
- If three.js fails to load (no internet), the rest of the UI still works; only the 3D panel stays empty.

## Devpost talking points the UI makes visible (judging criteria)

- **Trust & explainability:** every flag has a plain-English reason, a crop of the fax, what each reader saw, and the source of each check (PRISM KB / RxNorm / NPPES). The audit shows PRISM trace IDs.
- **Honest about uncertainty:** look-alikes are offered as options, never auto-picked; stock stays locked until the drug is confirmed; dose errors are transcribed as written, then flagged.
- **Design:** queue → decide → fill, keyboard shortcuts, no AI jargon up front. A pharmacist can use it without a walkthrough.
- **Required tools:** NicrronNinoo (readers) and Block Convey PRISM (tracing, guardrail, KB) are both named in the UI and the status bar.
- **Note on NicrronNinoo:** this UI was *not* generated with Ninoo Builder (the original plan in `PLAN.md`). On Devpost, credit Ninoo for what it actually did (the Nicrron vision readers on the backend), or run Ninoo Builder on `frontend/NINOO_BUILDER_PROMPT.md` and describe it honestly.

Deadline: Devpost submission by **4:30 PM EDT** (the team plan says submit by 4:15).
