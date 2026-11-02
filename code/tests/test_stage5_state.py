"""Stage 5 tests: state reconstruction, policy application, step generation.

Run with: ./.venv/bin/python3 code/tests/test_stage5_state.py

These exercise StateReconstructor directly (not just ProvenanceEngine),
using real dataset cases as anchors so every assertion is grounded in
actual evidence rather than synthetic fixtures, except where a scenario
(e.g. contradictory evidence) needs to be constructed to isolate it.
"""
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config
from pipeline.activity import ActivityLog
from pipeline.common import EvidenceRecord, Identifiers, SnapshotScope
from pipeline.evidence_loader import EvidenceStore
from pipeline.identities import IdentityResolver
from pipeline.provenance import ProvenanceEngine
from pipeline.state import StateReconstructor

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def find_op(steps, operation, resource_id=None):
    return next(
        (s for s in steps if s["operation"] == operation and (resource_id is None or s["resource_id"] == resource_id)),
        None,
    )


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    resolver = IdentityResolver(config.USER_IDENTITIES_CSV, config.USER_AUDIT_LOG_CSV)
    store = EvidenceStore()
    store.load_all(config.EVIDENCE_FILES_CSV, config.EVIDENCE_DIR)
    activity_log = ActivityLog(config.SYSTEM_ACTIVITY_CSV)
    engine = StateReconstructor(resolver, store, activity_log, config.DELETION_RULES_CSV)

    sample_rows = {r["case_id"]: r for r in csv.DictReader(open(config.SAMPLE_REQUESTS_CSV, newline="", encoding="utf-8"))}

    # === NOT_APPLICABLE: full pipeline, not just the classifier ===
    fake_row = dict(sample_rows["case_01"])
    fake_row["request_text"] = "Could you send me a copy of my data? Please don't delete anything."
    result = engine.evaluate_case(fake_row)
    check("NOT_APPLICABLE request produces status NOT_APPLICABLE", result.deletion_status == "NOT_APPLICABLE")
    check("NOT_APPLICABLE request produces an empty steps array", result.steps == [])

    # === Retention exception: SECURITY_AUDIT_LOG compliance (case_16) ===
    result = engine.evaluate_case(sample_rows["case_16"])
    present_step = find_op(result.steps, "VERIFY_RECORD_PRESENT", "audit_record_9528")
    check("retained security audit log gets VERIFY_RECORD_PRESENT (not deleted)", present_step is not None)
    if present_step:
        # case_16 overall is INCOMPLETE (other categories have violations),
        # and every verification step in an INCOMPLETE case is blanked to
        # evidence_id="" by design (confirmed against sample_output.csv
        # case_11) since it describes a post-remediation check.
        check(
            "retained audit log verification has empty evidence_id (INCOMPLETE case -> post-remediation check)",
            present_step["evidence_id"] == "",
        )
    check(
        "no DELETE_RECORD/REDACT_RECORD is ever proposed for a RETAIN-category security audit log",
        not any(
            s["resource_id"] == "audit_record_9528" and s["operation"] in ("DELETE_RECORD", "REDACT_RECORD")
            for s in result.steps
        ),
    )

    # === Retention exception in a COMPLETE case: evidence_id IS populated ===
    result = engine.evaluate_case(sample_rows["case_26"])
    check("case_26 (clean retained audit log, no other violations) is COMPLETE", result.deletion_status == "COMPLETE")
    present_step = find_op(result.steps, "VERIFY_RECORD_PRESENT")
    check(
        "in a COMPLETE case, the retained-record verification DOES cite real evidence",
        present_step is not None and present_step["evidence_id"] != "",
    )

    # === Tombstone exposure: tombstone_9178 (Kofi Addo) is PRESENT with PII, not absent ===
    result = engine.evaluate_case(sample_rows["case_32"])
    check("tombstone exposure case is INCOMPLETE", result.deletion_status == "INCOMPLETE")
    finding = find_op(result.steps, "REPORT_EXPOSED_PII", "tombstone_9178")
    check("exposed tombstone PII is reported as a FINDING, not treated as absent", finding is not None)
    if finding:
        check("tombstone exposure finding cites real evidence", finding["evidence_id"] != "")
        check("tombstone exposure finding is in FINDING phase", finding["phase"] == "FINDING")
    remediation = find_op(result.steps, "REDACT_RECORD", "tombstone_9178")
    check("tombstone remediation is REDACT (not DELETE -- the stable reference must survive)", remediation is not None)
    verify = find_op(result.steps, "VERIFY_RECORD_REDACTED", "tombstone_9178")
    check("tombstone verification checks REDACTED, with empty evidence_id (future check)", verify is not None and verify["evidence_id"] == "")

    # === Active data flow full cycle (case_11) ===
    result = engine.evaluate_case(sample_rows["case_11"])
    check("active data flow case is INCOMPLETE", result.deletion_status == "INCOMPLETE")
    f = find_op(result.steps, "REPORT_ACTIVE_DATA_FLOW", "data_flow_9522")
    r = find_op(result.steps, "DISABLE_DATA_FLOW", "data_flow_9522")
    v = find_op(result.steps, "VERIFY_DATA_FLOW_DISABLED", "data_flow_9522")
    check("active data flow produces FINDING -> REMEDIATION -> VERIFICATION triple", all([f, r, v]))
    if f and r and v:
        check("data flow finding cites evidence; remediation/verification do not", f["evidence_id"] != "" and r["evidence_id"] == "" and v["evidence_id"] == "")
        check(
            "data flow steps are correctly ordered (FINDING before REMEDIATION before VERIFICATION)",
            f["step_no"] < r["step_no"] < v["step_no"],
        )

    # === Backup / restore risk full cycle (case_09) ===
    result = engine.evaluate_case(sample_rows["case_09"])
    check("backup restore-risk case is INCOMPLETE", result.deletion_status == "INCOMPLETE")
    f = find_op(result.steps, "REPORT_RESTORE_RISK", "backup_4978")
    r = find_op(result.steps, "BLOCK_RESTORE", "backup_4978")
    v = find_op(result.steps, "VERIFY_RESTORE_BLOCKED", "backup_4978")
    vp = find_op(result.steps, "VERIFY_RECORD_PRESENT", "backup_4978")
    check("restore risk produces FINDING -> REMEDIATION -> VERIFY_RESTORE_BLOCKED", all([f, r, v]))
    check(
        "the backup archive itself is verified PRESENT (retained per policy, not deleted)",
        vp is not None and vp["state"] == "PRESENT",
    )

    # === Compliant backup (no restore risk): verify presence only, no finding ===
    # case_14's backup becomes compliant only after remediation in gold; here we
    # directly check a backup confirmed BLOCKED produces no finding.
    blocked_store = EvidenceStore()
    blocked_store._index(
        EvidenceRecord(
            file_id="test_blocked_backup",
            system_id="backup_service",
            category="BACKUP_ARCHIVE",
            resource_id="backup_test_001",
            state="BLOCKED",
            identifiers=Identifiers(emails={"blocked-test@example.com"}),
            captured_at=ts("2026-01-01T00:00:00Z"),
        )
    )
    blocked_prov = ProvenanceEngine(blocked_store)
    # Build a minimal reconstructor sharing only what _check_backup needs.
    mini = StateReconstructor.__new__(StateReconstructor)
    mini.provenance = blocked_prov
    outcomes = mini._check_backup("backup_service", "backup_test_001", ts("2026-01-02T00:00:00Z"))
    check(
        "a backup already BLOCKED produces no restore-risk finding, only compliant verifications",
        all(kind == "compliant" for kind, *_ in outcomes) and len(outcomes) == 2,
    )

    # === Contradictory / ambiguous evidence: never fabricate a confident answer ===
    # Two records for the same resource at the SAME timestamp disagree (one says
    # present-with-PII, one says absent) -- latest_direct_evidence must pick
    # deterministically (not silently average or guess), and a genuinely
    # unresolvable single-snapshot conflict must not be force-reported as COMPLETE.
    contradiction_store = EvidenceStore()
    t = ts("2026-01-01T00:00:00Z")
    contradiction_store._index(
        EvidenceRecord(
            file_id="evidence_contra_a",
            system_id="search_service",
            category="SEARCH_INDEX_ENTRY",
            resource_id="search_contra_001",
            state="FOUND",
            identifiers=Identifiers(emails={"contra@example.com"}),
            captured_at=t,
        )
    )
    contradiction_store._index(
        EvidenceRecord(
            file_id="evidence_contra_b",
            system_id="search_service",
            category="SEARCH_INDEX_ENTRY",
            resource_id="search_contra_001",
            state="NOT_FOUND",
            identifiers=Identifiers(),
            captured_at=t,
        )
    )
    contra_prov = ProvenanceEngine(contradiction_store)
    chosen = contra_prov.latest_direct_evidence("search_contra_001", t)
    check(
        "same-timestamp contradictory evidence resolves deterministically (not an exception/crash)",
        chosen is not None,
    )
    chosen_again = contra_prov.latest_direct_evidence("search_contra_001", t)
    check(
        "repeated resolution of the same contradiction is stable (same answer every time)",
        chosen.file_id == chosen_again.file_id,
    )

    # === No snapshot scope + no prior evidence anywhere => INCONCLUSIVE, category skipped ===
    empty_store = EvidenceStore()
    empty_prov = ProvenanceEngine(empty_store)
    absence = empty_prov.check_absence_by_snapshot(
        "nonexistent_resource_999", "analytics_platform", "ANALYTICS_PROFILE", t, user_resolved=True
    )
    check(
        "a category with zero evidence anywhere is INCONCLUSIVE even with user_resolved=True "
        "(the user-existence exception applies only to USER_PROFILE/ACCOUNT_TOMBSTONE)",
        absence.status == "INCONCLUSIVE",
    )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    else:
        print("All stage 5 (state reconstruction) checks passed.")


if __name__ == "__main__":
    main()
