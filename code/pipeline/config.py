"""Paths and constants shared across the pipeline.

All paths are resolved relative to this file so the solution works
regardless of the current working directory it is invoked from.
"""
from __future__ import annotations

from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent
DATASET_DIR = REPO_ROOT / "dataset"
EVIDENCE_DIR = DATASET_DIR / "evidence"

DELETION_REQUESTS_CSV = DATASET_DIR / "deletion_requests.csv"
SAMPLE_REQUESTS_CSV = DATASET_DIR / "sample_deletion_requests.csv"
SAMPLE_OUTPUT_CSV = DATASET_DIR / "sample_output.csv"
USER_IDENTITIES_CSV = DATASET_DIR / "user_identities.csv"
USER_AUDIT_LOG_CSV = DATASET_DIR / "user_audit_log.csv"
SYSTEM_ACTIVITY_CSV = DATASET_DIR / "system_activity.csv"
DELETION_RULES_CSV = DATASET_DIR / "deletion_rules.csv"
EVIDENCE_FILES_CSV = DATASET_DIR / "evidence_files.csv"

OUTPUT_CSV = REPO_ROOT / "output.csv"

CACHE_DIR = CODE_DIR / ".cache"
OCR_CACHE_DIR = CACHE_DIR / "ocr"
LLM_CACHE_DIR = CACHE_DIR / "llm"

# Local Ollama configuration (no cloud API, no API key).
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b"
OLLAMA_TIMEOUT_SECONDS = 120
