"""Run the full pipeline against the 32 sample cases and compare against
sample_output.csv, field by field. Reports every mismatch; does not tune
the implementation to force a match (per explicit instruction).

Run with: ./.venv/bin/python3 code/tests/sample_comparison.py
"""
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config
from pipeline.activity import ActivityLog
from pipeline.evidence_loader import EvidenceStore
from pipeline.identities import IdentityResolver
from pipeline.state import StateReconstructor


def main():
    t0 = time.time()
    resolver = IdentityResolver(config.USER_IDENTITIES_CSV, config.USER_AUDIT_LOG_CSV)
    store = EvidenceStore()
    store.load_all(config.EVIDENCE_FILES_CSV, config.EVIDENCE_DIR)
    activity_log = ActivityLog(config.SYSTEM_ACTIVITY_CSV)
    engine = StateReconstructor(resolver, store, activity_log, config.DELETION_RULES_CSV)
    print(f"setup took {time.time()-t0:.1f}s\n")

    sample_requests = list(csv.DictReader(open(config.SAMPLE_REQUESTS_CSV, newline="", encoding="utf-8")))
    golden = {r["case_id"]: r for r in csv.DictReader(open(config.SAMPLE_OUTPUT_CSV, newline="", encoding="utf-8"))}

    status_match = 0
    status_mismatch = []
    step_exact_match = 0
    step_mismatches = []

    for row in sample_requests:
        case_id = row["case_id"]
        t1 = time.time()
        result = engine.evaluate_case(row)
        elapsed = time.time() - t1

        gold = golden[case_id]
        gold_status = gold["deletion_status"]
        gold_steps = json.loads(gold["steps_json"])

        status_ok = result.deletion_status == gold_status
        if status_ok:
            status_match += 1
        else:
            status_mismatch.append((case_id, result.deletion_status, gold_status))

        steps_ok = result.steps == gold_steps
        if steps_ok:
            step_exact_match += 1
        else:
            step_mismatches.append((case_id, result, gold_steps))

        flag = "OK" if (status_ok and steps_ok) else ("STATUS+STEPS DIFF" if not status_ok else "STEPS DIFF")
        print(f"{case_id}: status={result.deletion_status} (gold={gold_status}) [{flag}] ({elapsed:.1f}s)")
        if result.skipped_categories:
            print(f"    skipped (undeterminable): {result.skipped_categories}")
        if result.notes:
            for n in result.notes:
                print(f"    note: {n}")

    print(f"\n=== SUMMARY ===")
    print(f"status match: {status_match}/32")
    print(f"exact step match: {step_exact_match}/32")

    print(f"\n=== STATUS MISMATCHES ({len(status_mismatch)}) ===")
    for case_id, got, want in status_mismatch:
        print(f"  {case_id}: got {got}, expected {want}")

    print(f"\n=== STEP-LEVEL DIFF DETAIL (first 10) ===")
    for case_id, result, gold_steps in step_mismatches:
        print(f"\n--- {case_id} (status got={result.deletion_status}) ---")
        print("  MINE:", json.dumps(result.steps))
        print("  GOLD:", json.dumps(gold_steps))

    print(f"\ntotal wall time: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
