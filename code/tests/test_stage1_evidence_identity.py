"""Stage 1 smoke tests: evidence parsers + identity resolution.

Run with: python3 code/tests/test_stage1_evidence_identity.py
No third-party dependencies required (stdlib only).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config
from pipeline.evidence_json import parse_json_file
from pipeline.evidence_loader import EvidenceStore
from pipeline.evidence_sql import parse_sql_file
from pipeline.evidence_text import parse_handover_txt, parse_log_file
from pipeline.identities import IdentityResolver

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def main():
    # --- Identity resolution ---
    resolver = IdentityResolver(config.USER_IDENTITIES_CSV, config.USER_AUDIT_LOG_CSV)
    check("loads all 850 users", len(resolver.users) == 850)

    uid = resolver.resolve_email("amina.elmasri@gmail.com")
    check("resolves current email", uid == "user_01")

    uid_ci = resolver.resolve_email("AMINA.ELMASRI@GMAIL.COM")
    check("resolves case-insensitively", uid_ci == "user_01")

    # every row in deletion_requests.csv must resolve to *some* user
    import csv

    unresolved = 0
    with open(config.DELETION_REQUESTS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if resolver.resolve_email(row["email"]) is None:
                unresolved += 1
    check(f"all 518 request emails resolve to a user (0 unresolved, got {unresolved})", unresolved == 0)

    # --- SQL parser ---
    sql_recs, sql_scopes = parse_sql_file(
        config.EVIDENCE_DIR / "databases" / "customer_data_inventory_20260524.sql", "test_sql"
    )
    check("SQL inventory parser extracts records", len(sql_recs) > 0)
    check(
        "SQL inventory records have no unclassified category",
        all(r.category for r in sql_recs),
    )

    replica_recs, replica_scopes = parse_sql_file(
        config.EVIDENCE_DIR / "databases" / "customer_accounts_failover_replica_20260902.sql",
        "evidence_54",
    )
    resource_ids = {r.resource_id for r in replica_recs}
    check(
        "failover replica SQL exposes both replica and primary rows",
        {"account_005", "account_8741"} <= resource_ids,
    )

    # --- JSON parser ---
    json_recs = parse_json_file(
        config.EVIDENCE_DIR / "records" / "customer_data_snapshot_bundle_20260915.json",
        "test_json",
    )
    check("JSON snapshot bundle parser extracts records", len(json_recs) > 0)
    tombstones = [r for r in json_recs if r.category == "ACCOUNT_TOMBSTONE"]
    check("JSON parser finds ACCOUNT_TOMBSTONE records", len(tombstones) > 0)
    redacted_tombstone = next((r for r in tombstones if r.resource_id == "tombstone_440"), None)
    check(
        "redacted tombstone (tombstone_440) carries no identifiers",
        redacted_tombstone is not None and redacted_tombstone.identifiers.is_empty(),
    )
    exposed_tombstone = next((r for r in tombstones if r.resource_id == "tombstone_9178"), None)
    check(
        "non-redacted tombstone (tombstone_9178) still exposes identifiers",
        exposed_tombstone is not None and not exposed_tombstone.identifiers.is_empty(),
    )

    service_export_recs = parse_json_file(
        config.EVIDENCE_DIR / "records" / "service_resource_export_20260915_114455.json",
        "evidence_92",
    )
    se_ids = {r.resource_id for r in service_export_recs}
    check(
        "service_resource_export exposes known resource ids from sample_output",
        {"account_6440", "analytics_5335", "audit_record_3737"} <= se_ids,
    )

    # --- Text / log parsers ---
    handover_recs = parse_handover_txt(
        config.EVIDENCE_DIR / "records" / "customer_account_handover_20261001.txt", "evidence_03"
    )
    check("handover txt parser extracts all 41 lines", len(handover_recs) == 41)
    check(
        "handover parser normalizes ALL-CAPS email",
        any(
            r.resource_id == "account_1810" and "choi.jihoon94@gmail.com" in r.identifiers.emails
            for r in handover_recs
        ),
    )

    log_recs = parse_log_file(
        config.EVIDENCE_DIR / "logs" / "service_events_ap_south_20260915.log", "evidence_01"
    )
    check("log parser extracts completed-event records", len(log_recs) > 0)
    check(
        "log parser correlates identifier from started-line via trace_id",
        any(r.resource_id == "search_1874" and r.identifiers.emails for r in log_recs),
    )

    # --- Full evidence store load ---
    store = EvidenceStore()
    store.load_all(config.EVIDENCE_FILES_CSV, config.EVIDENCE_DIR)
    check("evidence store loads all 94 files' metadata", len(store.file_meta) == 94)
    check(
        "evidence store has non-zero records from non-image/pdf files (83 of 94)",
        len(store.records) > 2000,
    )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    else:
        print("All stage 1 checks passed.")


if __name__ == "__main__":
    main()
