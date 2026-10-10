"""
Lane: Backend (Koushik)
Input guardrail. Fax text is UNTRUSTED: it runs through this before any model
sees it.

Two layers:
1. PRISM Guardrail (dashboard rule: Prompt Injection + PII/PHI Detection on the
   `verifill-guard` agent). On HTTP/SDK paths PRISM flags, it doesn't block,
   so we read the flag and enforce the block ourselves.
2. Local pattern check below, so the demo works even if PRISM is slow.

An order with a guardrail hit -> status "rejected" or "needs_pharmacist",
never auto-filled. Test with samples RX-009 (injection fax, TODO).
"""
import re

INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|rules)",
    r"\bsystem\s*:", r"you are now", r"mark (all )?(fields )?(as )?verified",
    r"skip (the )?pharmacist", r"auto[- ]?(fill|approve)", r"do not flag",
]


def local_check(text: str) -> list[str]:
    hits = []
    low = text.lower()
    for p in INJECTION_PATTERNS:
        if re.search(p, low):
            hits.append(f"possible prompt injection: /{p}/")
    return hits


def check(text: str, session_id: str) -> list[str]:
    """Return guardrail flags for this text. TODO: add PRISM flag read-back."""
    from backend import tracing
    flags = local_check(text)
    with tracing.step("guard", session_id, input_obj=text) as t:
        t["output"] = {"flags": flags}
    return flags
