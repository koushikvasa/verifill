"""
Lane: Backend (stretch)
Agentic zoom-and-reread for low-confidence fields.

TODO: for a field bbox: crop with padding -> upscale 3x (LANCZOS) -> try up to
config.ZOOM_MAX_TRIES variants (median denoise, dilate strokes, different
threshold) -> OCR psm 7 + one vision call on the crop -> majority vote.
Still no agreement -> keep field amber/red. Log every try to the audit trail.
"""


def reread(img, bbox, field_name, session_id):
    raise NotImplementedError
