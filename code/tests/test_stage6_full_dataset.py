"""Stage 6 full-dataset invariants: checks that go beyond pure structural
validation (validate_output.py) into whether every citation in the real
518-case output.csv is actually grounded in real, timely, participant-
visible evidence. Run with: ./.venv/bin/python3 code/tests/test_stage6_full_dataset.py

Requires output.csv to already exist (run code/main.py first).
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config
from pipeline.common import parse_timestamp

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def main():
    if not config.OUTPUT_CSV.exists():
        print("FATAL: output.csv does not exist. Run code/main.py first.")
        sys.exit(1)

    rows = list(csv.DictReader(open(config.OUTPUT_CSV, newline="", encoding="utf-8")))
    requests = {
        r["case_id"]: r for r in csv.DictReader(open(config.DELETION_REQUESTS_CSV, newline="", encoding="utf-8"))
    }
    evidence_files = {
        r["file_id"]: r for r in csv.DictReader(open(config.EVIDENCE_FILES_CSV, newline="", encoding="utf-8"))
    }

    check("exactly 518 data rows", len(rows) == 518)
    check("every output case_id exists in deletion_requests.csv", all(r["case_id"] in requests for r in rows))

    all_cited_ids = set()
    future_evidence_violations = []
    unknown_evidence_ids = []
    empty_steps_not_not_applicable = 0
    not_applicable_with_steps = 0

    for row in rows:
        case_id = row["case_id"]
        status = row["deletion_status"]
        steps = json.loads(row["steps_json"])
        audit_at = parse_timestamp(requests[case_id]["audit_at"])

        if status == "NOT_APPLICABLE" and steps != []:
            not_applicable_with_steps += 1
        if status != "NOT_APPLICABLE" and not steps:
            empty_steps_not_not_applicable += 1

        for step in steps:
            eid = step.get("evidence_id")
            if not eid:
                continue
            all_cited_ids.add(eid)
            if eid not in evidence_files:
                unknown_evidence_ids.append((case_id, eid))
                continue
            captured_at = parse_timestamp(evidence_files[eid]["captured_at"])
            if captured_at and audit_at and captured_at > audit_at:
                future_evidence_violations.append((case_id, eid, captured_at.isoformat(), audit_at.isoformat()))

    check("no NOT_APPLICABLE case has non-empty steps_json", not_applicable_with_steps == 0)
    check(
        "every cited evidence_id exists in evidence_files.csv (never fabricated)",
        len(unknown_evidence_ids) == 0,
    )
    if unknown_evidence_ids:
        for cid, eid in unknown_evidence_ids[:10]:
            print(f"    unknown evidence_id: {cid} -> {eid}")

    check(
        "no cited evidence was captured after that case's own audit_at (no future-evidence leakage)",
        len(future_evidence_violations) == 0,
    )
    if future_evidence_violations:
        for cid, eid, cap, audit in future_evidence_violations[:10]:
            print(f"    FUTURE EVIDENCE: {cid} cites {eid} captured {cap} but audit_at={audit}")

    print(f"\n{len(all_cited_ids)} distinct evidence files cited across the full run "
          f"(of {len(evidence_files)} total evidence files)")
    print(f"COMPLETE/INCOMPLETE cases with zero steps: {empty_steps_not_not_applicable} (flagged, not necessarily an error -- see audit_summary.py)")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    else:
        print("All stage 6 (full-dataset invariant) checks passed.")


if __name__ == "__main__":
    main()
