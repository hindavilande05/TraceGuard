"""Stage 4 tests: local Ollama (qwen2.5:7b) intent classification.

Run with: python3 code/tests/test_stage4_classifier.py
Requires a running local Ollama server with qwen2.5:7b pulled.

IMPORTANT: dataset/sample_deletion_requests.csv (the only labeled sample
data available) contains 32 requests, ALL of which are genuine deletion
requests (0 NOT_APPLICABLE cases) -- confirmed from sample_output.csv.
Running the classifier against it can only demonstrate that it does not
produce false negatives on clear deletion requests; it CANNOT demonstrate
NOT_APPLICABLE accuracy, because the sample contains no such examples.
The adversarial cases below (drawn from the real, participant-facing
dataset/deletion_requests.csv -- not organizer-only data, and not hidden
answers, just requests whose text we read ourselves) are what actually
exercises non-deletion classification.
"""
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config
from pipeline.classifier import classify_request_text

FAILURES = []
RESULTS_LOG = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def main():
    import requests

    # --- 1. Verify exact model name and connectivity ---
    try:
        tags = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=10).json()
        model_names = [m["name"] for m in tags.get("models", [])]
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: cannot reach Ollama at {config.OLLAMA_BASE_URL}: {exc}")
        sys.exit(1)

    check(f"Ollama is reachable and lists configured model '{config.OLLAMA_MODEL}'", config.OLLAMA_MODEL in model_names)
    print(f"  (models available: {model_names})")

    t0 = time.time()
    r = classify_request_text("Please delete my account and all personal data.", use_cache=False)
    check("Ollama responds to a basic classification request", r.source == "model")
    check("basic deletion request classified correctly", r.authorizes_deletion is True and r.category == "DELETION")
    print(f"  (first live call took {time.time()-t0:.1f}s, eval_count={r.eval_count}, prompt_eval_count={r.prompt_eval_count})")

    # --- 2. All 32 sample requests (known: all are genuine deletion requests) ---
    sample_rows = list(csv.DictReader(open(config.SAMPLE_REQUESTS_CSV, newline="", encoding="utf-8")))
    check("sample_deletion_requests.csv has 32 rows", len(sample_rows) == 32)

    sample_results = []
    for row in sample_rows:
        res = classify_request_text(row["request_text"])
        sample_results.append((row["case_id"], res))
        RESULTS_LOG.append((row["case_id"], row["request_text"][:80], res))

    false_negatives = [(cid, r) for cid, r in sample_results if not r.authorizes_deletion]
    print(f"\nSample set (32 known genuine deletion requests, 0 NOT_APPLICABLE in this sample):")
    print(f"  classified as authorizing deletion: {32 - len(false_negatives)}/32")
    if false_negatives:
        print("  FALSE NEGATIVES (should all authorize deletion):")
        for cid, r in false_negatives:
            print(f"    {cid}: category={r.category} confidence={r.confidence} reasoning={r.reasoning!r}")
    check(
        "0 false negatives on the 32 known-genuine sample deletion requests "
        "(this does NOT test NOT_APPLICABLE accuracy -- the sample has no such cases)",
        len(false_negatives) == 0,
    )
    low_conf = [(cid, r) for cid, r in sample_results if r.confidence == "low"]
    print(f"  low-confidence (flagged needs_review) on sample set: {len(low_conf)}/32")

    # --- 3. Adversarial cases from the real (participant-facing) dataset ---
    # Each (case_id, request_text, expected_authorizes_deletion, expected_category)
    # read directly from dataset/deletion_requests.csv and manually verified by
    # reading the English text ourselves -- not organizer-only data, not a
    # hidden golden label.
    full_rows = {r["case_id"]: r for r in csv.DictReader(open(config.DELETION_REQUESTS_CSV, newline="", encoding="utf-8"))}

    adversarial = [
        (
            "case_537",
            False,
            "CORRECTION",
            "correction-only request ('correct it without removing any of my records')",
        ),
        (
            "case_542",
            False,
            "ACCESS_OR_EXPORT",
            "access/export request ('send me a copy', 'don't delete or change anything')",
        ),
        (
            "case_534",
            False,
            "MARKETING_OPT_OUT",
            "marketing opt-out, explicitly 'not asking for deletion'",
        ),
        (
            "case_53",
            True,
            "DELETION",
            "genuine deletion request padded with 'apply the correct treatment' boilerplate "
            "that must NOT be mistaken for a correction request",
        ),
        (
            "case_03",
            True,
            "DELETION",
            "genuine deletion request written in French (multilingual check)",
        ),
        (
            "case_06",
            True,
            "DELETION",
            "follow-up: account gone, but a leftover search result should also be removed",
        ),
        (
            "case_25",
            True,
            "DELETION",
            "follow-up: asking to verify whether an earlier deletion request actually completed",
        ),
        (
            "case_32",
            True,
            "DELETION",
            "follow-up: account gone, but an internal tombstone record still shows PII to redact",
        ),
    ]
    # case_03 is a sample-only case_id (sample_deletion_requests.csv uses
    # its own case_NN numbering, disjoint from deletion_requests.csv).
    sample_by_id = {r["case_id"]: r for r in sample_rows}

    print("\nAdversarial cases (real dataset text, manually read and verified):")
    adversarial_failures = []
    for case_id, expected_auth, expected_cat, description in adversarial:
        text = (full_rows.get(case_id) or sample_by_id[case_id])["request_text"]
        res = classify_request_text(text)
        RESULTS_LOG.append((case_id, text[:80], res))
        ok = res.authorizes_deletion == expected_auth
        print(
            f"  {case_id} [{description}]\n"
            f"    text: {text[:120]!r}\n"
            f"    got: authorizes_deletion={res.authorizes_deletion} category={res.category} "
            f"confidence={res.confidence}\n"
            f"    reasoning: {res.reasoning!r}"
        )
        check(f"{case_id}: authorizes_deletion == {expected_auth} ({description})", ok)
        if not ok:
            adversarial_failures.append(case_id)
        # category is softer-checked (reported, not hard-failed) since the
        # boundary between e.g. ACCESS_OR_EXPORT and OTHER_NON_DELETION can
        # be a reasonable judgement call either way, but authorizes_deletion
        # must be correct.
        if res.category != expected_cat:
            print(f"    NOTE: category differs from expected ({expected_cat}) -- not a hard failure")

    # --- 4. Caching ---
    text = full_rows["case_53"]["request_text"]
    r1 = classify_request_text(text)
    check("second call for the same request_text is served from cache", r1.cache_hit is True)

    # --- 5. Malformed/ambiguous handling: verify fallback path behaves conservatively ---
    from pipeline.classifier import _fallback_heuristic

    fb_delete = _fallback_heuristic("Please delete my account.")
    check("fallback heuristic flags DELETE-like text for review, not silently", fb_delete.needs_review is True)
    fb_other = _fallback_heuristic("What's your return policy?")
    check("fallback heuristic defaults unmatched text to non-deletion", fb_other.authorizes_deletion is False)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    else:
        print("All stage 4 (classifier) checks passed.")


if __name__ == "__main__":
    main()
