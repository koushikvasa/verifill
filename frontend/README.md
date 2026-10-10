# Mock API responses
Captured from the running backend (`python -m scripts.demo_check` order). Shapes are exact; ids/timestamps change per run.

| file | what it shows |
|---|---|
| RX-001_ready_to_fill.json | all green, pick ticket in stock (Aisle 3 · B · 12), full audit incl. safety gate |
| RX-001_scan_ok.json / RX-001_scan_wrong.json | `/scan` match and WRONG PRODUCT |
| RX-003_lookalike_before_resolve.json | drug red with `candidates`, "itching" as evidence only, pick ticket blocked |
| RX-003_after_resolve_drug.json | after `POST /resolve {field:"drug", value:"hydroxyzine", pharmacist}`: confirmed, bin unlocked |
| RX-004_decimal_error_callback.json | 0.5 mg transcribed as written, red with KB-grounded checks, `callback_draft` |
| RX-004_resolve_refused_409.json | confirming a failing value without `override_reason` |
| RX-009_rejected_injection.json | `rejected`, `guardrail_flags`, no fields |
| RX-011_three_orders.json | one fax → 3 orders sharing `fax_id` |
| RX-016_denied_by_prescriber.json | refill denied, reason in `callback_draft` |
| RX-018_not_prescription.json | lab results filed, no order |
| RX-008_worst_case.json | everything amber/red with reasons, nothing fillable |
