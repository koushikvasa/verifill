"""
Lane: Backend
Turns readings + checks into one value and a color per field, with reasons.

GREEN (verified): >=2 readers agree (rapidfuzz ratio >= AGREE_RATIO) AND OCR/
    model confidence >= OCR_GREEN AND every check passed.
AMBER (review): only one legible reader, or confidence between OCR_AMBER and
    OCR_GREEN, or zoom still unsure.
RED (conflict): readers disagree, any check failed, or LASA candidate exists.

Order -> ready_to_fill only if every CRITICAL_FIELD is green and no guardrail flags.
PRISM Evaluator: attach to the `verifill-score` agent's output (field verdicts)
in the dashboard; eval.py also scores verdicts vs ground_truth.json.
"""


def score_field(field):
    raise NotImplementedError


def score_order(order):
    raise NotImplementedError
