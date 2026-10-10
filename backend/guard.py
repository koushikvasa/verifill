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
    r"ignore (all |any |the )?(previous|prior|above|other|safety|verification)? ?(instructions|rules|checks?|warnings?|guidelines)",
    r"\bsystem\s*:", r"you are now",
    r"mark(ed)? (all |this |the )?(fields? |order |prescription |rx )?(as )?(verified|ready|approved|safe|valid)",
    r"skip (the |all |any )?(pharmacist|review|verification|checks?|validation)",
    r"auto[- ]?(fill|approve|approval)", r"approve[sd]? (this |it )?automatically",
    r"do not (flag|review|question|verify|check)",
    r"(note|message|instructions?) (to|for) (the )?(ai|model|system|assistant|bot|software|verifill|pipeline)",
    r"\b(ai|assistant|model|llm|bot)\s*[,:]\s*(please )?",
    r"(already|has been|was) (been )?(verified|approved|reviewed|checked|validated) (by|and)",
    r"(no|without) (further |additional |human |pharmacist )?(review|verification|checks?) (is )?(needed|required|necessary)",
    r"(treat|consider|process) (this |it )?(as )?(verified|approved|safe|trusted|routine)",
    r"output .{0,30}(verified|ready_to_fill|approved)", r"ready[_ ]to[_ ]fill",
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
