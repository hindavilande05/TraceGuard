"""Stage 2 tests: resource-ownership provenance + absence-by-omission.

Run with: python3 code/tests/test_stage2_provenance.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config
from pipeline.common import Identifiers
from pipeline.evidence_loader import EvidenceStore
from pipeline.evidence_sql import parse_sql_file
from pipeline.identities import IdentityResolver
from pipeline.provenance import ProvenanceEngine

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def make_store_from_sql(*paths_and_ids):
    store = EvidenceStore()
    for path, file_id in paths_and_ids:
        recs, scopes = parse_sql_file(path, file_id)
        for r in recs:
            store._index(r)
        for s in scopes:
            store.snapshots.append(s)
            store.snapshots_by_scope[(s.system_id, s.category)].append(s)
    return store


def main():
    # === Ambiguous ownership: bare-name matches must never grant ownership ===
    store = make_store_from_sql(
        (config.EVIDENCE_DIR / "databases" / "customer_search_primary_20260910.sql", "evidence_test_search")
    )
    engine = ProvenanceEngine(store)
    name_only = Identifiers(names={"Jonas Berg"})
    matches = engine.find_resources_for_user(name_only, ts("2026-09-10T08:45:00Z"))
    check(
        "bare-name identifiers never resolve to resource ownership (decoy safety)",
        matches == [],
    )
    # but the *real* strong identifier for that same person does resolve
    strong = Identifiers(emails={"jonas.berg@outlook.com"})
    strong_matches = engine.find_resources_for_user(strong, ts("2026-09-10T08:45:00Z"))
    check(
        "strong identifier (email) resolves correctly despite a near-duplicate name decoy",
        any(m.resource_id == "search_001" for m in strong_matches),
    )
    check(
        "near-duplicate decoy (Jonas Berglund) is NOT pulled in by the real Jonas Berg's email",
        all(m.resource_id != "search_8814" for m in strong_matches),
    )

    # === Incomplete / unknown snapshot scope -> INCONCLUSIVE, never ABSENT ===
    empty_store = EvidenceStore()
    empty_engine = ProvenanceEngine(empty_store)
    result = empty_engine.check_absence_by_snapshot(
        "account_999", "account_service", "USER_PROFILE", ts("2026-09-01T00:00:00Z")
    )
    check(
        "no snapshot roster at all -> INCONCLUSIVE (never silently ABSENT)",
        result.status == "INCONCLUSIVE",
    )

    # === Replica drift: resource present in a regional replica but absent from primary ===
    drift_store = make_store_from_sql(
        (
            config.EVIDENCE_DIR / "databases" / "customer_data_regional_replicas_20260519.sql",
            "evidence_drift",
        )
    )
    drift_engine = ProvenanceEngine(drift_store)
    as_of = ts("2026-05-19T06:00:00Z")

    established = drift_engine.established_presence("account_585", "account_service", "USER_PROFILE", as_of)
    check(
        "replica row for account_585 establishes prior presence (has identifiers)",
        established is not None and "zofia.z024@outlook.com" in established.identifiers.emails,
    )

    absence = drift_engine.check_absence_by_snapshot("account_585", "account_service", "USER_PROFILE", as_of)
    check(
        "account_585 is absent from the authoritative *primary* table -> ABSENT_CONFIRMED, even though a "
        "same-timestamp replica still has it (primary is authoritative for 'was it deleted')",
        absence.status == "ABSENT_CONFIRMED" and absence.file_id == "evidence_drift",
    )

    drift = drift_engine.find_drift("account_585", "account_service", "USER_PROFILE", as_of)
    check(
        "find_drift separately flags that a non-primary replica still holds the deleted resource "
        "(restore/recreation risk, distinct from the primary deletion check)",
        any(s.table_name == "customer_data_replica_ap_south" for s in drift),
    )
    check(
        "find_drift does not flag the eu_west replica, which correctly has no copy",
        all(s.table_name != "customer_data_replica_eu_west" for s in drift),
    )

    # === Historical identifiers: a resource known only under an old email still resolves ===
    resolver = IdentityResolver(config.USER_IDENTITIES_CSV, config.USER_AUDIT_LOG_CSV)
    import csv

    audit_rows = list(csv.DictReader(open(config.USER_AUDIT_LOG_CSV, newline="", encoding="utf-8")))
    email_changes = [r for r in audit_rows if r["field_name"] == "email" and r["old_value"]]
    check("user_audit_log.csv has at least one historical email change to test with", len(email_changes) > 0)
    if email_changes:
        sample = email_changes[0]
        user_rec = resolver.get(sample["user_id"])
        check(
            "historical email is retained on the user's identifier set",
            sample["old_value"].lower() in user_rec.all_identifiers().emails,
        )

    # === No future evidence: as_of cutoff must exclude later-captured records ===
    future_store = EvidenceStore()
    from pipeline.common import EvidenceRecord

    future_store._index(
        EvidenceRecord(
            file_id="future_file",
            system_id="account_service",
            category="USER_PROFILE",
            resource_id="account_777",
            state="FOUND",
            identifiers=Identifiers(emails={"future@example.com"}),
            captured_at=ts("2026-12-01T00:00:00Z"),
        )
    )
    future_engine = ProvenanceEngine(future_store)
    cutoff = ts("2026-09-01T00:00:00Z")
    none_before_cutoff = future_engine.latest_direct_evidence("account_777", cutoff)
    check(
        "evidence captured after audit_at is excluded from latest_direct_evidence",
        none_before_cutoff is None,
    )
    found_after_cutoff = future_engine.latest_direct_evidence("account_777", ts("2027-01-01T00:00:00Z"))
    check(
        "the same evidence IS visible once as_of is moved past its captured_at",
        found_after_cutoff is not None,
    )

    # === Active sync/restore path: a DATA_FLOW / BACKUP_ARCHIVE resource is discoverable ===
    real_store = EvidenceStore()
    real_store.load_all(config.EVIDENCE_FILES_CSV, config.EVIDENCE_DIR)
    real_engine = ProvenanceEngine(real_store)
    active_flows = [r for r in real_store.records if r.category == "DATA_FLOW" and r.state == "ACTIVE"]
    check("at least one ACTIVE DATA_FLOW record exists in the real dataset", len(active_flows) > 0)
    if active_flows:
        sample_flow = active_flows[0]
        ids = Identifiers()
        ids.emails |= sample_flow.identifiers.emails
        ids.user_refs |= sample_flow.identifiers.user_refs
        if ids.emails or ids.user_refs:
            matches = real_engine.find_resources_for_user(ids, sample_flow.captured_at)
            check(
                "an active data flow's own resource is discoverable via its own identifiers at its own capture time",
                any(m.resource_id == sample_flow.resource_id for m in matches),
            )

    # === Real case_01 scenario: zero evidence anywhere for account_001/user_01 ===
    full_store = EvidenceStore()
    full_store.load_all(config.EVIDENCE_FILES_CSV, config.EVIDENCE_DIR)
    full_engine = ProvenanceEngine(full_store)
    case01_as_of = ts("2026-09-03T11:30:00Z")

    no_evidence_at_all = full_engine.latest_direct_evidence("account_001", case01_as_of)
    check(
        "sanity: account_001 truly has zero direct evidence anywhere (matches manual grep)",
        no_evidence_at_all is None,
    )

    without_implication = full_engine.check_absence_by_snapshot(
        "account_001", "account_service", "USER_PROFILE", case01_as_of, user_resolved=False
    )
    check(
        "without the user-existence exception, zero-evidence account_001 is INCONCLUSIVE (not silently ABSENT)",
        without_implication.status == "INCONCLUSIVE",
    )

    with_implication = full_engine.check_absence_by_snapshot(
        "account_001", "account_service", "USER_PROFILE", case01_as_of, user_resolved=True
    )
    check(
        "with user_resolved=True, the explicit USER_PROFILE exception confirms ABSENT citing evidence_54, "
        "matching sample_output.csv's case_01 answer",
        with_implication.status == "ABSENT_CONFIRMED" and with_implication.file_id == "evidence_54",
    )

    non_profile_category_unaffected = full_engine.check_absence_by_snapshot(
        "support_999999", "support_platform", "SUPPORT_MESSAGE", case01_as_of, user_resolved=True
    )
    check(
        "the user-existence exception does NOT extend to non-profile categories like SUPPORT_MESSAGE",
        non_profile_category_unaffected.status == "INCONCLUSIVE",
    )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    else:
        print("All stage 2 (provenance) checks passed.")


if __name__ == "__main__":
    main()
