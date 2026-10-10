# Devpost: accuracy, uncertainty and trust (backend numbers, 14:40)

Everything below is reproducible from the repo: `python -m scripts.eval` (19 sample faxes), `python -m scripts.robustness` (12-file judge kit), `python -m pytest tests` (43 offline tests).

## Headline numbers (19 faxes, 247 ground-truth fields)
- **Auto-accept precision: 100%.** Every one of the 175 fields Verifill marked green equals the ground truth.
- **False "ready to fill": 0.** No order was cleared for filling that should not have been. This number is enforced by code, not by luck: a final safety gate re-checks every rule on the finished order and downgrades it if any is violated.
- **Coverage: 71%** of fields auto-accepted. The other 29% go to the pharmacist with a reason. We optimise for precision over coverage on purpose: a wrong green is a dispensing error, an unnecessary amber is thirty seconds of a pharmacist's time.
- **Flag recall: 81%** of the expected safety flags were raised (the misses are wording mismatches in the test set, not missed behaviours).
- **Outcome accuracy: 18/19** faxes end in the expected state. The one miss is a freeform note where one reader could not read the sig frequency: it went to the pharmacist instead of green, which is the safe direction.

## Judge kit (12 adversarial uploads): 12/12, zero crashes
Shorthand sigs ("i po qd", "ii po bid"), a liquid in mL, a drug outside our data (losartan, confirmed live via RxNav), a resent fax, a politely worded prompt injection, a faint near-invisible injection, a blank page, pure noise, a sideways page, a 340 px thumbnail, and a 3-page Group-4 TIFF with the prescription on page 3.

## How "never guesses" is implemented
1. **Transcribe, never correct.** Levothyroxine "0.5 mg" stays 0.5 mg in the order and is flagged as 10x the marketed 0.05 mg, grounded in the PRISM Knowledge Base. A partly legible drug name is returned as "Hy????zine", never completed.
2. **Three independent readers, two must agree.** Claude and GPT (different vendors, via NicrronNinoo) plus a non-LLM OCR vote; a second OCR engine confirms the agreed value is physically on the page. Numbers must match exactly: "1 tab" and "7 tab" never agree.
3. **Every check is grounded** in the PRISM KB, RxNav or NPPES, with the source recorded on the check (`grounded_in`). Look-alike drugs are shown as candidates with evidence; the pharmacist picks. Inventory never resolves an ambiguous drug.
4. **Fax text is data, never instructions.** A guardrail runs on every page before any model call; reader outputs are scanned too, so faint injected text the OCR cannot see is still caught.
5. **Pharmacist in the loop, with an audit trail.** Confirming a value re-runs every check; overriding a failed check needs a typed reason. Every stage is traced in PRISM and the whole order is submitted as a trajectory for the Evaluator.

## What it does not do
It does not read every fax. On the worst sample (RX-008) it reads almost nothing and says so. The claim is narrower and stronger: **it never fills what it cannot verify.**
