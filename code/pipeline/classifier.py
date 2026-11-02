"""Local Ollama (qwen2.5:7b) intent classification: does request_text
actually authorize deletion, or is it an access/export/correction/
marketing-opt-out/other request that happens to sit in the same dataset?

This is the ONLY step in the pipeline that calls a model. Everything else
(identity resolution, evidence parsing, policy application, state
reconstruction, output generation) is deterministic Python -- per the
approved architecture. No cloud API is used; this talks to a local Ollama
server only.

Design constraints (explicit requirements):
  - Every response is schema-validated before being trusted.
  - Every (model, prompt_version, request_text) call is cached to disk so
    repeated runs are free and deterministic given unchanged inputs.
  - A malformed or low-confidence response does not silently become a
    confident answer: it falls back to a conservative, explainable
    heuristic and is flagged so it can be reviewed (see
    ClassificationResult.needs_review).
  - Real usage (prompt/response tokens, latency) is recorded from
    Ollama's own response fields for the usage report -- nothing
    fabricated.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from . import config

PROMPT_VERSION = "v2"

_SYSTEM_PROMPT = """You are a data-privacy intake classifier for an e-commerce platform's deletion-audit system.
You will be given one customer message (request_text), possibly written in a language other than English.
Decide whether this message is an explicit request to delete the customer's account and/or personal data.

Respond with ONLY a JSON object with exactly these fields:
{
  "authorizes_deletion": true or false,
  "category": one of "DELETION", "ACCESS_OR_EXPORT", "CORRECTION", "MARKETING_OPT_OUT", "OTHER_NON_DELETION",
  "confidence": one of "high", "medium", "low",
  "reasoning": a short (<30 word) explanation in English
}

Rules:
- A request to close/delete/erase the account or remove personal data is "DELETION" with authorizes_deletion=true,
  even if it also asks you to "apply the correct treatment" or confirm when done -- that boilerplate does not change the intent.
- IMPORTANT: many messages are FOLLOW-UPS about a deletion that was already requested earlier, not the original request.
  These are STILL "DELETION" with authorizes_deletion=true. This includes:
    * asking to check/confirm whether a deletion actually completed ("please check if my data was really removed",
      "the page still says it failed, please verify")
    * reporting that the account is gone but one specific trace still remains, and asking for that trace to be
      removed too ("my profile still shows up in search, please remove that too", "an internal record still shows
      my details, please remove the rest but keep only the reference you must retain")
  Do not classify these as "CORRECTION" (they are not asking to fix/edit a detail -- they want the leftover trace
  GONE or redacted) or as "ACCESS_OR_EXPORT" (asking "please check/confirm/tell me whether it's deleted" is a
  deletion-verification request, not a request to receive a copy of their data).
- A request to see, download, or receive a copy of the customer's own data, where the customer is NOT asking about
  a deletion and is NOT asking anything to be removed, is "ACCESS_OR_EXPORT" with authorizes_deletion=false.
- A request to fix/update/correct a detail (name, address, etc.), where nothing is being deleted or removed, is
  "CORRECTION" with authorizes_deletion=false.
- A request to stop marketing emails/opt out of marketing, without asking to delete the whole account or any data, is "MARKETING_OPT_OUT" with authorizes_deletion=false.
- Anything else that does not ask for deletion, removal, or verification of a deletion is "OTHER_NON_DELETION" with authorizes_deletion=false.
- The decisive question is: does the customer want something about them permanently GONE (now, or confirmed gone
  from an earlier request)? If yes -> DELETION/true. If they only want to view, fix, or stop receiving something,
  with nothing removed -> false.
- If the message is genuinely ambiguous, set confidence to "low" rather than guessing with high confidence.
"""

VALID_CATEGORIES = {"DELETION", "ACCESS_OR_EXPORT", "CORRECTION", "MARKETING_OPT_OUT", "OTHER_NON_DELETION"}
VALID_CONFIDENCE = {"high", "medium", "low"}

# Conservative fallback: if the model is unreachable or never returns a
# valid schema after retries, fall back to a narrow, explainable keyword
# check rather than guessing. This only fires in that failure path -- the
# normal path always relies on the model's own judgement, which is what
# correctly distinguishes "please delete my account" from "please correct
# my address", something a naive keyword match cannot do (see the
# adversarial test cases).
_FALLBACK_DELETE_PATTERNS = re.compile(
    r"\b(delete|erase|remove|close).{0,40}(account|data|profile|information)\b"
    r"|suppress(ion)?.{0,20}compte"  # French: "suppression de mon compte"
    r"|eliminar.{0,20}cuenta",  # Spanish: "eliminar mi cuenta"
    re.IGNORECASE,
)


@dataclass
class ClassificationResult:
    authorizes_deletion: bool
    category: str
    confidence: str
    reasoning: str
    source: str  # "model" | "fallback_heuristic"
    needs_review: bool
    raw_model_output: Optional[str] = None
    prompt_eval_count: Optional[int] = None
    eval_count: Optional[int] = None
    total_duration_ns: Optional[int] = None
    cache_hit: bool = False


def _cache_key(request_text: str) -> str:
    h = hashlib.sha256(f"{config.OLLAMA_MODEL}|{PROMPT_VERSION}|{request_text}".encode("utf-8")).hexdigest()
    return h


def _cache_path(key: str) -> Path:
    config.LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.LLM_CACHE_DIR / f"{key}.json"


def _validate_schema(obj: dict) -> bool:
    if not isinstance(obj, dict):
        return False
    if not isinstance(obj.get("authorizes_deletion"), bool):
        return False
    if obj.get("category") not in VALID_CATEGORIES:
        return False
    if obj.get("confidence") not in VALID_CONFIDENCE:
        return False
    if not isinstance(obj.get("reasoning"), str):
        return False
    return True


def _fallback_heuristic(request_text: str) -> ClassificationResult:
    matched = bool(_FALLBACK_DELETE_PATTERNS.search(request_text))
    return ClassificationResult(
        authorizes_deletion=matched,
        category="DELETION" if matched else "OTHER_NON_DELETION",
        confidence="low",
        reasoning="model unavailable or returned no valid response after retries; "
        "conservative keyword fallback used",
        source="fallback_heuristic",
        needs_review=True,
    )


def _call_ollama_once(request_text: str):
    import requests

    prompt = f'request_text: """{request_text}"""'
    resp = requests.post(
        f"{config.OLLAMA_BASE_URL}/api/generate",
        json={
            "model": config.OLLAMA_MODEL,
            "system": _SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        },
        timeout=config.OLLAMA_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()


def classify_request_text(request_text: str, use_cache: bool = True) -> ClassificationResult:
    key = _cache_key(request_text)
    cache_path = _cache_path(key)

    if use_cache and cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        data["cache_hit"] = True
        return ClassificationResult(**data)

    result: Optional[ClassificationResult] = None
    last_raw = None
    for attempt in range(2):
        try:
            payload = _call_ollama_once(request_text)
        except Exception:  # noqa: BLE001 - network/availability failure -> conservative fallback
            break
        raw_text = payload.get("response", "")
        last_raw = raw_text
        try:
            parsed = json.loads(raw_text)
        except (TypeError, ValueError):
            parsed = None

        if parsed is not None and _validate_schema(parsed):
            result = ClassificationResult(
                authorizes_deletion=parsed["authorizes_deletion"],
                category=parsed["category"],
                confidence=parsed["confidence"],
                reasoning=parsed["reasoning"],
                source="model",
                needs_review=(parsed["confidence"] == "low"),
                raw_model_output=raw_text,
                prompt_eval_count=payload.get("prompt_eval_count"),
                eval_count=payload.get("eval_count"),
                total_duration_ns=payload.get("total_duration"),
            )
            break
        # invalid schema -> retry once with the same call (deterministic
        # temperature=0 means a retry is mainly useful for transient
        # formatting glitches, not a different "opinion")

    if result is None:
        result = _fallback_heuristic(request_text)
        result.raw_model_output = last_raw

    cache_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    return result
