"""Per-case audit summary for the full 518-case run: status distribution,
classification confidence/fallback usage, resource-attribution
abstentions, inconclusive-evidence categories, and active recreation
risks found. Run with: ./.venv/bin/python3 code/tests/audit_summary.py

This is diagnostic reporting, not a pass/fail test -- it feeds
evaluation_report.md and the per-case audit requested for review.
"""
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config
from pipeline.activity import ActivityLog
from pipeline.evidence_loader import EvidenceStore
from pipeline.identities import IdentityResolver
from pipeline.state import StateReconstructor
from pipeline.usage import UsageTracker


def case_sort_key(case_id: str) -> int:
    return int(case_id.rsplit("_", 1)[1])


def main():
    t0 = time.time()
    resolver = IdentityResolver(config.USER_IDENTITIES_CSV, config.USER_AUDIT_LOG_CSV)
    store = EvidenceStore()
    store.load_all(config.EVIDENCE_FILES_CSV, config.EVIDENCE_DIR)
    activity_log = ActivityLog(config.SYSTEM_ACTIVITY_CSV)
    usage = UsageTracker(model=config.OLLAMA_MODEL)
    engine = StateReconstructor(resolver, store, activity_log, config.DELETION_RULES_CSV, usage_tracker=usage)

    with open(config.DELETION_REQUESTS_CSV, newline="", encoding="utf-8") as f:
        requests = list(csv.DictReader(f))
    requests.sort(key=lambda r: case_sort_key(r["case_id"]))

    status_counts = Counter()
    low_confidence_cases = []
    fallback_cases = []
    account_abstentions = []
    other_skipped = []
    active_data_flow_cases = []
    restore_risk_cases = []
    tombstone_exposure_cases = []
    zero_step_complete_cases = []
    zero_evidence_incomplete_cases = []
    unresolved_identity_cases = []

    for row in requests:
        case_id = row["case_id"]
        result = engine.evaluate_case(row)
        status_counts[result.deletion_status] += 1

        if result.classification_confidence == "low":
            low_confidence_cases.append((case_id, result.classification_category))
        if result.classification_source == "fallback_heuristic":
            fallback_cases.append(case_id)
        if "identity could not be resolved" in " ".join(result.notes):
            unresolved_identity_cases.append(case_id)

        for skip in result.skipped_categories:
            if skip.startswith("USER_PROFILE"):
                account_abstentions.append(case_id)
            else:
                other_skipped.append((case_id, skip))

        if result.deletion_status == "COMPLETE" and not result.steps:
            zero_step_complete_cases.append(case_id)
        if result.deletion_status == "INCOMPLETE" and not result.steps:
            zero_evidence_incomplete_cases.append(case_id)

        for step in result.steps:
            if step["operation"] == "REPORT_ACTIVE_DATA_FLOW":
                active_data_flow_cases.append((case_id, step["resource_id"]))
            if step["operation"] == "REPORT_RESTORE_RISK":
                restore_risk_cases.append((case_id, step["resource_id"]))
            if step["operation"] == "REPORT_EXPOSED_PII" and step["system_id"] == "identity_service":
                tombstone_exposure_cases.append((case_id, step["resource_id"]))

    usage.finish()
    elapsed = time.time() - t0

    print(f"=== STATUS DISTRIBUTION ({len(requests)} cases, {elapsed:.1f}s) ===")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count} ({100*count/len(requests):.1f}%)")

    print(f"\n=== CLASSIFICATION CONFIDENCE ===")
    print(f"  low-confidence classifications (flagged needs_review): {len(low_confidence_cases)}")
    for cid, cat in low_confidence_cases[:20]:
        print(f"    {cid}: category={cat}")
    print(f"  fallback-heuristic classifications (model unavailable/invalid): {len(fallback_cases)}")
    for cid in fallback_cases[:20]:
        print(f"    {cid}")

    print(f"\n=== RESOURCE-ATTRIBUTION ABSTENTIONS ===")
    print(f"  cases where the account resource_id could not be discovered from participant-visible data: {len(account_abstentions)} ({100*len(account_abstentions)/len(requests):.1f}%)")
    print(f"  other skipped (inconclusive) category checks: {len(other_skipped)}")
    other_skip_categories = Counter(s.split(":")[0].split(" ")[0] for _, s in other_skipped)
    for cat, count in other_skip_categories.most_common():
        print(f"    {cat}: {count}")

    print(f"\n=== ACTIVE RECREATION RISKS FOUND (evidence-backed, as of audit_at) ===")
    print(f"  active data flow findings: {len(active_data_flow_cases)}")
    print(f"  restore-risk findings: {len(restore_risk_cases)}")

    print(f"\n=== TOMBSTONE PII EXPOSURE FINDINGS ===")
    print(f"  {len(tombstone_exposure_cases)} cases with exposed tombstone PII")
    for cid, rid in tombstone_exposure_cases:
        print(f"    {cid}: {rid}")

    print(f"\n=== OTHER UNRESOLVED UNCERTAINTY ===")
    print(f"  COMPLETE cases with zero verification steps (nothing confirmable found): {len(zero_step_complete_cases)}")
    print(f"  INCOMPLETE-with-zero-steps (no evidence anywhere, cannot assert COMPLETE): {len(zero_evidence_incomplete_cases)}")
    for cid in zero_evidence_incomplete_cases:
        print(f"    {cid}")
    for cid in zero_step_complete_cases[:20]:
        print(f"    {cid}")
    print(f"  cases with unresolved identity (should be 0): {len(unresolved_identity_cases)}")

    print(
        f"\n=== CLASSIFIER USAGE ===\n"
        f"  live calls: {usage.live_calls}, cache hits: {usage.cache_hits}, fallback: {usage.fallback_calls}\n"
        f"  total tokens: {usage.total_tokens} (prompt={usage.total_prompt_tokens}, completion={usage.total_completion_tokens})\n"
        f"  wall clock: {usage.wall_clock_seconds:.1f}s"
    )


if __name__ == "__main__":
    main()
