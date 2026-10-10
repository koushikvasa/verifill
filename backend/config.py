"""Settings, read once from environment / .env."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "backend" / "data"
SAMPLES_DIR = ROOT / "samples" / "faxes"

# Nicrron (NicrronNinoo unified model API). Base URL + model ids come from the
# sponsor guide; the client assumes an OpenAI-compatible chat endpoint.
NICRRON_API_KEY = os.getenv("NICRRON_API_KEY", "")
NICRRON_BASE_URL = os.getenv("NICRRON_BASE_URL", "")
READER_A_MODEL = os.getenv("READER_A_MODEL", "")   # vision model, vendor 1
READER_B_MODEL = os.getenv("READER_B_MODEL", "")   # vision model, different vendor

# PRISM by Block Convey
PRISMTRACE_API_KEY = os.getenv("PRISMTRACE_API_KEY", "")
PRISMTRACE_PROJECT_ID = os.getenv("PRISMTRACE_PROJECT_ID", "")
PRISMTRACE_HOST = os.getenv("PRISMTRACE_HOST", "https://prism.blockconvey.com")
AGENT_PREFIX = os.getenv("AGENT_PREFIX", "verifill")

# Public verification APIs (no keys). OFFLINE=1 uses backend/data only.
OFFLINE = os.getenv("VERIFILL_OFFLINE", "1") == "1"
RXNAV_URL = "https://rxnav.nlm.nih.gov/REST"
NPPES_URL = "https://npiregistry.cms.hhs.gov/api/"

# Scoring thresholds
OCR_GREEN = float(os.getenv("OCR_GREEN", "0.80"))
OCR_AMBER = float(os.getenv("OCR_AMBER", "0.55"))
AGREE_RATIO = float(os.getenv("AGREE_RATIO", "0.90"))   # fuzzy match ratio for "readers agree"
ZOOM_MAX_TRIES = int(os.getenv("ZOOM_MAX_TRIES", "3"))
