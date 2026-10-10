"""
Lane: Data
Score the pipeline against samples/faxes/ground_truth.json. These numbers go
on Devpost and the demo slide.

TODO, report:
- auto-accept precision: of fields marked green, % that equal ground truth   (target: ~100%)
- coverage: % of all fields marked green                                     (honest number, will be low on bad faxes)
- flag recall: % of expected_flags that the pipeline raised
- outcome accuracy: order status == expected_outcome
- false "ready_to_fill" count                                               (must be 0)
Also send each order's verdicts to PRISM so the Evaluator can re-check them.

Run: python -m scripts.eval   (from repo root)
"""
