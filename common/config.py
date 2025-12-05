import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

# Storage mode: local / gcp / azure (later)
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")

LOCAL_INPUT_DIR = BASE_DIR / "data" / "input"
LOCAL_OUTPUT_DIR = BASE_DIR / "data" / "output"
LOCAL_JOBS_FILE = BASE_DIR / "data" / "jobs.json"

# Ensure dirs exist (for local mode)
LOCAL_INPUT_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
if not LOCAL_JOBS_FILE.exists():
    LOCAL_JOBS_FILE.write_text("[]")
