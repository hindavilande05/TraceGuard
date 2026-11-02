"""Load dataset/evidence_files.csv, parse every evidence file with the
format-appropriate parser, and build lookup indexes used by state
reconstruction: by resource_id and by identifier (email/phone/hash/user_id).
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from .common import EvidenceRecord, SnapshotScope, parse_timestamp
from .evidence_json import parse_json_file
from .evidence_sql import parse_sql_file
from .evidence_text import parse_handover_txt, parse_log_file


class EvidenceStore:
    def __init__(self):
        self.file_meta: Dict[str, dict] = {}
        self.records: List[EvidenceRecord] = []
        self.by_resource: Dict[str, List[EvidenceRecord]] = defaultdict(list)
        self.by_identifier: Dict[str, List[EvidenceRecord]] = defaultdict(list)
        self.snapshots: List[SnapshotScope] = []
        # (system_id, category) -> list of SnapshotScope, for completeness checks.
        self.snapshots_by_scope: Dict[tuple, List[SnapshotScope]] = defaultdict(list)

    def _index(self, rec: EvidenceRecord):
        self.records.append(rec)
        if rec.resource_id:
            self.by_resource[rec.resource_id].append(rec)
        for value in rec.all_identifier_values():
            self.by_identifier[value].append(rec)

    def load_all(self, evidence_files_csv: Path, evidence_root: Path, ocr_parser="default"):
        if ocr_parser == "default":
            from .evidence_ocr import parse_ocr_evidence as ocr_parser
        with open(evidence_files_csv, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        for row in rows:
            file_id = row["file_id"]
            rel_path = row["file_path"]
            self.file_meta[file_id] = {
                "path": rel_path,
                "origin": row["origin"],
                "captured_at": parse_timestamp(row["captured_at"]),
            }
            full_path = evidence_root.parent / rel_path
            ext = full_path.suffix.lower()

            try:
                if ext == ".sql":
                    recs, scopes = parse_sql_file(full_path, file_id)
                    for scope in scopes:
                        self.snapshots.append(scope)
                        self.snapshots_by_scope[(scope.system_id, scope.category)].append(scope)
                elif ext == ".json":
                    recs = parse_json_file(full_path, file_id)
                elif ext == ".txt":
                    recs = parse_handover_txt(full_path, file_id)
                elif ext == ".log":
                    recs = parse_log_file(full_path, file_id)
                elif ext in (".png", ".jpg", ".jpeg", ".pdf"):
                    recs = ocr_parser(full_path, file_id) if ocr_parser else []
                else:
                    recs = []
            except Exception as exc:  # noqa: BLE001 - never let one bad file kill the run
                print(f"[evidence_loader] WARNING: failed to parse {rel_path}: {exc}")
                recs = []

            file_captured_at = self.file_meta[file_id]["captured_at"]
            for rec in recs:
                if rec.captured_at is None:
                    rec.captured_at = file_captured_at
                elif file_captured_at is not None and file_captured_at > rec.captured_at:
                    # A record's own embedded timestamp (e.g. one line in
                    # an aggregated log) can predate the file's own
                    # evidence_files.csv manifest captured_at (when the
                    # file was exported/collected). The fact only became
                    # knowable to an auditor once the file itself existed,
                    # so the later of the two bounds the earliest audit_at
                    # this record may be cited for -- otherwise a case
                    # audited before the file was ever exported could cite
                    # a file it could not possibly have seen yet.
                    rec.captured_at = file_captured_at
                self._index(rec)

        return self

    def find_for_identifiers(self, identifiers) -> List[EvidenceRecord]:
        """All evidence records that mention any of the given identifiers
        (an Identifiers instance), deduplicated by object identity."""
        seen = set()
        results = []
        for value in (
            identifiers.emails
            | identifiers.phones
            | identifiers.email_hashes
            | identifiers.user_refs
        ):
            for rec in self.by_identifier.get(value, []):
                if id(rec) not in seen:
                    seen.add(id(rec))
                    results.append(rec)
        return results
