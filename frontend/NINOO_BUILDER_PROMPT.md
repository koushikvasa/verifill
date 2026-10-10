# Paste this into Ninoo Builder to generate the review UI

Build a single-page web app called **Verifill**, a pharmacist review console for faxed prescriptions. Clean clinical look: white background, slate text, one accent color (teal), status colors green #16a34a / amber #d97706 / red #dc2626. Desktop-first, works on a laptop screen.

Backend: FastAPI at `http://localhost:8000` (make the base URL a config constant). Endpoints:
- `GET /samples` → list of sample faxes `[{id, file, severity, note}]`
- `GET /samples/{file}` → fax image
- `POST /faxes/sample/{id}` → runs the pipeline on one fax, returns a **list** of Orders (a fax can hold several drugs, or none)
- `POST /orders/sample/{id}` → same, first Order only (legacy)
- `POST /orders/upload` (multipart `file`) → Order
- `POST /orders/{order_id}/resolve` body `{field, value, pharmacist}` → updated Order
- `POST /orders/{order_id}/scan` body `{ndc}` → `{match: bool, detail}`

Order shape: `{id, fax_file, fax_id, page, fax_type: "new_rx"|"refill_authorization"|"cover_sheet"|"other", orientation_fixed, status: "ready_to_fill"|"needs_pharmacist"|"rejected"|"not_prescription"|"denied_by_prescriber", guardrail_flags: string[], fields: {<name>: {name, value, status: "verified"|"review"|"conflict"|"confirmed", confidence, reasons: string[], candidates: string[], readings: [{source, value, confidence, bbox}], checks: [{name, passed, detail, grounded_in}]}}, pick_ticket: {status: "in_stock"|"partial"|"out_of_stock"|"blocked", item: {ndc, drug, strength, manufacturer, lot, expiry, aisle, shelf, bin}, qty_needed, qty_to_pick, owed, substitution, substitution_requires_approval, warnings: string[]}, callback_draft, audit: [{at, actor, action, detail, trace_id}]}`

Layout:
1. **Left sidebar: Fax inbox.** List of samples with a severity pill, plus an "Upload fax" button. Clicking runs the pipeline and shows a loading state with the stages: Guardrail → Reading → Verifying → Inventory.
2. **Center: the fax image** with colored boxes drawn over each field using `readings[].bbox` (green/amber/red by field status). Hovering a field row highlights its box. Zoom and pan.
3. **Right: the order.** Big status banner at top: "Ready to fill" (green), "Needs pharmacist" (amber), or "Rejected: guardrail" (red, listing the flags). Then one row per field: label, value, status chip, confidence bar. Expanding a row shows *why*: the reasons, what each reader saw (side by side, disagreements highlighted), and each check with pass/fail and where it was grounded ("PRISM KB", "RxNorm", "NPPES"). If `candidates` exist (look-alike drugs), show them as buttons; the pharmacist must pick one. Every amber/red row has Confirm and Correct buttons that call `/resolve`.
4. **Pick ticket card** under the fields. If status is "blocked", show a lock: "Inventory locked until the drug is verified. Stock never decides an ambiguous drug." Otherwise show drug, NDC, a big location badge "Aisle 3 · Shelf B · Bin 12", lot + expiry, qty to pick, owed, warnings, and any substitution with an "Approve substitution" button. A "Scan bottle NDC" input calls `/scan`.
5. **Callback draft** (if present): a message to the prescriber with a Copy button.
6. **Audit trail drawer** at the bottom: timeline of audit events, each with its PRISM trace id.

When a fax returns several orders, show them as tabs ("Medication 1 of 3"). `not_prescription` shows a grey "Filed: not a prescription" card; `denied_by_prescriber` shows the prescriber's reason. If `orientation_fixed` ≠ 0, show a small "auto-rotated 180°" chip.

Never show "ready to fill" unless the API says so. No fake data in the UI; everything comes from the API.
