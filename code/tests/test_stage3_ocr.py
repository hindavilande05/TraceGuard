"""Stage 3 tests: OCR extraction from the real PNG/PDF evidence files.

Run with: python3 code/tests/test_stage3_ocr.py
Requires: tesseract (system binary) + pytesseract, PyMuPDF, Pillow (venv).

These assert on the *facts that must survive OCR* (specific identifiers,
resource ids, lifecycle states) rather than exact OCR byte-for-byte output,
since OCR of rendered UI text can have minor character-level noise.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config
from pipeline.evidence_ocr import extract_raw_text, parse_ocr_evidence

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def main():
    images_dir = config.EVIDENCE_DIR / "images"
    docs_dir = config.EVIDENCE_DIR / "documents"

    # --- account_admin_console: USER_PROFILE still fully identifiable ---
    text = extract_raw_text(images_dir / "account_admin_console_20260926.png", "test_account_console")
    check("OCR produced non-trivial text for account_admin_console", len(text.strip()) > 50)
    recs = parse_ocr_evidence(images_dir / "account_admin_console_20260926.png", "test_account_console")
    check("account_admin_console yields at least one record", len(recs) > 0)
    account_rec = next((r for r in recs if r.resource_id == "account_5762"), None)
    check("account_admin_console record uses the correct resource_id (account_5762)", account_rec is not None)
    if account_rec:
        check(
            "account_admin_console record exposes the visible email",
            "haruka.s1838@zoho.com" in account_rec.identifiers.emails,
        )
        check(
            "account_admin_console record exposes the visible user_id as a user_ref",
            "user_11" in account_rec.identifiers.user_refs,
        )
        check("account_admin_console record classified as USER_PROFILE", account_rec.category == "USER_PROFILE")

    # --- deleted_account_reference: a REDACTED tombstone (no PII visible) ---
    recs = parse_ocr_evidence(
        images_dir / "deleted_account_reference_20260310.png", "test_tombstone_console"
    )
    check("deleted_account_reference yields at least one record", len(recs) > 0)
    if recs:
        check(
            "deleted_account_reference record carries the subject's user_ref (user_17)",
            any("user_17" in r.identifiers.user_refs for r in recs),
        )
        check(
            "deleted_account_reference record exposes NO email/phone/hash (fully redacted)",
            all(r.identifiers.is_empty() or (not r.identifiers.emails and not r.identifiers.phones) for r in recs),
        )

    # --- backup_restore_console: BACKUP_ARCHIVE with an owner email ---
    recs = parse_ocr_evidence(
        images_dir / "backup_restore_console_20260429.png", "test_backup_console"
    )
    backup_rec = next((r for r in recs if r.resource_id == "backup_9287"), None)
    check("backup_restore_console identifies backup_9287", backup_rec is not None)
    if backup_rec:
        check(
            "backup_9287 record exposes the owner email from the detail panel",
            "areed1433@gmail.com" in backup_rec.identifiers.emails,
        )
        check("backup_9287 classified as BACKUP_ARCHIVE", backup_rec.category == "BACKUP_ARCHIVE")

    # --- connector_management_console: an ACTIVE data flow ---
    recs = parse_ocr_evidence(
        images_dir / "connector_management_console_20260807.png", "test_connector_console"
    )
    flow_rec = next((r for r in recs if r.resource_id == "data_flow_1121"), None)
    check("connector_management_console identifies data_flow_1121", flow_rec is not None)
    if flow_rec:
        check(
            "data_flow_1121 record exposes the selector email",
            "jain.aditi@gmail.com" in flow_rec.identifiers.emails,
        )
        check("data_flow_1121 inferred as ACTIVE (continuous sync)", flow_rec.state == "ACTIVE")
        check("data_flow_1121 classified as DATA_FLOW", flow_rec.category == "DATA_FLOW")

    # --- legal_retention_register.pdf: multi-subject order/user_id rows ---
    pdf_text = extract_raw_text(
        docs_dir / "legal_retention_register_20260529.pdf", "test_legal_register"
    )
    check("PDF text extraction produced non-trivial text", len(pdf_text.strip()) > 100)
    recs = parse_ocr_evidence(docs_dir / "legal_retention_register_20260529.pdf", "test_legal_register")
    check("legal retention register yields multiple distinct order rows", len(recs) >= 5)
    row = next((r for r in recs if r.resource_id == "ORD-20264118"), None)
    check("legal retention register correctly pairs ORD-20264118 with user_08", row is not None and "user_08" in row.identifiers.user_refs)
    check(
        "legal retention register rows are NOT cross-contaminated with other rows' user_ids",
        row is not None and "user_12" not in row.identifiers.user_refs,
    )
    check("legal retention register rows classified as LEGAL_HOLD_RECORD", all(r.category == "LEGAL_HOLD_RECORD" for r in recs))

    # --- OCR cache: second call must not re-run OCR (reads cache file) ---
    cache_path = config.OCR_CACHE_DIR / "test_account_console.txt"
    check("OCR cache file was written to disk", cache_path.exists())
    cached_text = extract_raw_text(images_dir / "account_admin_console_20260926.png", "test_account_console")
    check("second call returns identical cached text", cached_text == text)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    else:
        print("All stage 3 (OCR) checks passed.")


if __name__ == "__main__":
    main()
