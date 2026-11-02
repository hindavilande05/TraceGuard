"""Structural validation of the generated output.csv against the full
submission contract (AGENTS.md ssec 6.3/6.4, problem_statement.md).

Run with: ./.venv/bin/python3 code/tests/validate_output.py

This does NOT check correctness against any golden answer (there is none
for the full 518-case set) -- only structural conformance: valid JSON,
allowed values, required ordering, consecutive numbering, and correct
evidence_id handling per phase/status.
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config
from pipeline.steps import PHASE_ORDER, _OPERATION_ORDER

ALLOWED_STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
REQUIRED_STEP_FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}

FINDING_OPS = {"REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"}
REMEDIATION_OPS = {"DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"}
VERIFICATION_OPS = {
    "VERIFY_RECORD_ABSENT",
    "VERIFY_RECORD_PRESENT",
    "VERIFY_RECORD_REDACTED",
    "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED",
    "VERIFY_RESTORE_BLOCKED",
}
OPS_BY_PHASE = {"FINDING": FINDING_OPS, "REMEDIATION": REMEDIATION_OPS, "VERIFICATION": VERIFICATION_OPS}

VALID_STATES = {
    "PRESENT",
    "ABSENT",
    "ACTIVE",
    "DISABLED",
    "BLOCKED",
    "REDACTED",
    "ANONYMIZED",
}

errors = []
warnings = []


def err(case_id, msg):
    errors.append(f"{case_id}: {msg}")


def warn(case_id, msg):
    warnings.append(f"{case_id}: {msg}")


def validate_steps(case_id, status, steps):
    if status == "NOT_APPLICABLE":
        if steps != []:
            err(case_id, f"NOT_APPLICABLE must have steps_json=[], got {len(steps)} steps")
        return

    if not steps and status == "COMPLETE":
        warn(case_id, "COMPLETE with zero verification steps (no confirmable category found)")
    if not steps and status == "INCOMPLETE":
        warn(
            case_id,
            "INCOMPLETE with zero steps: no participant-visible evidence exists anywhere for this "
            "subject, so COMPLETE could not be asserted (requires evidence) but no specific finding "
            "could be cited either (never fabricated) -- see evaluation_report.md",
        )

    expected_no = 1
    last_phase_rank = -1
    last_sort_key = None
    seen_phases_in_order = []
    for step in steps:
        missing = REQUIRED_STEP_FIELDS - set(step.keys())
        extra = set(step.keys()) - REQUIRED_STEP_FIELDS
        if missing:
            err(case_id, f"step {step.get('step_no')} missing fields {missing}")
        if extra:
            err(case_id, f"step {step.get('step_no')} has unexpected fields {extra}")

        if step.get("step_no") != expected_no:
            err(case_id, f"step_no not consecutive: expected {expected_no}, got {step.get('step_no')}")
        expected_no += 1

        phase = step.get("phase")
        if phase not in PHASE_ORDER:
            err(case_id, f"invalid phase {phase!r}")
            continue
        if not seen_phases_in_order or seen_phases_in_order[-1] != phase:
            seen_phases_in_order.append(phase)
        phase_rank = PHASE_ORDER[phase]
        if phase_rank < last_phase_rank:
            err(case_id, f"phase out of order at step {step['step_no']}: {phase} after a later phase")
        last_phase_rank = phase_rank

        op = step.get("operation")
        if op not in OPS_BY_PHASE.get(phase, set()):
            err(case_id, f"operation {op!r} not valid for phase {phase!r} at step {step['step_no']}")

        state = step.get("state")
        if state not in VALID_STATES:
            err(case_id, f"invalid state {state!r} at step {step['step_no']}")

        evidence_id = step.get("evidence_id")
        if phase == "REMEDIATION" and evidence_id != "":
            err(case_id, f"REMEDIATION step {step['step_no']} must have empty evidence_id, got {evidence_id!r}")
        if status == "INCOMPLETE" and phase == "VERIFICATION" and evidence_id != "":
            err(case_id, f"INCOMPLETE VERIFICATION step {step['step_no']} must have empty evidence_id, got {evidence_id!r}")
        if phase == "FINDING" and evidence_id == "":
            err(case_id, f"FINDING step {step['step_no']} must cite real evidence, got empty evidence_id")
        if status == "COMPLETE" and phase == "VERIFICATION" and evidence_id == "":
            err(case_id, f"COMPLETE VERIFICATION step {step['step_no']} must cite real evidence, got empty evidence_id")

        key = (
            phase_rank,
            _OPERATION_ORDER.get(op, 999),
            step.get("system_id") or "",
            step.get("resource_id") or "",
        )
        if last_sort_key is not None and key < last_sort_key:
            err(case_id, f"step {step['step_no']} violates required ordering (operation/system_id/resource_id)")
        last_sort_key = key

    if status == "INCOMPLETE" and steps:
        if "FINDING" not in seen_phases_in_order:
            err(case_id, "INCOMPLETE case has steps but no FINDING phase")
        if seen_phases_in_order != sorted(set(seen_phases_in_order), key=lambda p: PHASE_ORDER[p]):
            err(case_id, f"phases not in FINDING/REMEDIATION/VERIFICATION order: {seen_phases_in_order}")


def main():
    if not config.OUTPUT_CSV.exists():
        print(f"FATAL: {config.OUTPUT_CSV} does not exist. Run code/main.py first.")
        sys.exit(1)

    with open(config.OUTPUT_CSV, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(csv.DictReader(open(config.OUTPUT_CSV, newline="", encoding="utf-8")))

    print(f"header: {header}")
    if header != ["case_id", "deletion_status", "steps_json"]:
        err("<file>", f"header must be exactly case_id,deletion_status,steps_json, got {header}")

    print(f"row count: {len(rows)}")
    if len(rows) != 518:
        err("<file>", f"expected exactly 518 data rows, got {len(rows)}")

    case_ids = [r["case_id"] for r in rows]
    if len(set(case_ids)) != len(case_ids):
        err("<file>", "duplicate case_id values found")

    expected_ids = set(
        r["case_id"] for r in csv.DictReader(open(config.DELETION_REQUESTS_CSV, newline="", encoding="utf-8"))
    )
    missing_ids = expected_ids - set(case_ids)
    extra_ids = set(case_ids) - expected_ids
    if missing_ids:
        err("<file>", f"missing {len(missing_ids)} case_ids from deletion_requests.csv, e.g. {sorted(missing_ids)[:5]}")
    if extra_ids:
        err("<file>", f"output has {len(extra_ids)} case_ids not in deletion_requests.csv, e.g. {sorted(extra_ids)[:5]}")

    sort_keys = [int(cid.rsplit("_", 1)[1]) for cid in case_ids]
    if sort_keys != sorted(sort_keys):
        err("<file>", "rows are not sorted by case_id (numeric order)")

    status_counts = {}
    for row in rows:
        case_id = row["case_id"]
        status = row["deletion_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
        if status not in ALLOWED_STATUSES:
            err(case_id, f"invalid deletion_status {status!r}")
            continue
        try:
            steps = json.loads(row["steps_json"])
        except (TypeError, ValueError) as exc:
            err(case_id, f"steps_json is not valid JSON: {exc}")
            continue
        if not isinstance(steps, list):
            err(case_id, "steps_json is not a JSON array")
            continue
        validate_steps(case_id, status, steps)

    print(f"\nstatus distribution: {status_counts}")

    print(f"\n{len(errors)} ERROR(S), {len(warnings)} WARNING(S)")
    for e in errors[:50]:
        print("ERROR:", e)
    if len(errors) > 50:
        print(f"... and {len(errors) - 50} more errors")
    for w in warnings[:20]:
        print("WARNING:", w)
    if len(warnings) > 20:
        print(f"... and {len(warnings) - 20} more warnings")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
