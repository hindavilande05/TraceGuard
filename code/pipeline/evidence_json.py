"""Parse the five JSON evidence shapes found under dataset/evidence/records/:

1. ``{"snapshots": [{"captured_at", "data_store", "collection", "records": [...]}]}``
   -- records_assurance snapshot bundles. A collection named
   ``operational_record_history`` nests a generic per-record ledger
   (recorded_at/system/resource_id/record_type/result/lookup_value)
   instead of direct fields.
2. ``{"origin"/"record_count", "records": [{"recorded_at","system",
   "resource_id","record_type","result","subject_reference"}]}``
   -- connector_configurations / customer_export_manifest /
   security_audit_ledger style ledgers.
3. ``{"captured_at"/"source", "collection", "records": [...]}`` with direct
   fields per record -- customer_export_jobs / customer_analytics_report.
4. ``{"service_exports": {<system>: {<field_name>: [entries...]}}}``
   -- service_resource_export point-in-time readbacks, some entries nest a
   ``profile`` dict or a ``hits.documents`` list.

Dispatch is based on which top-level keys are present, since the shape
(not the filename) identifies the format.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .common import EvidenceRecord, classify_category, extract_identifiers, infer_state, infer_system, parse_timestamp


def _flatten_one_level(d: dict) -> dict:
    flat = {}
    for k, v in d.items():
        if isinstance(v, dict):
            flat.update(v)
        else:
            flat[k] = v
    return flat


def _record_from_ledger_entry(file_id, entry, fallback_captured_at):
    category = classify_category(entry.get("record_type"), entry.get("resource_id"))
    system_id = infer_system(category, entry.get("system"))
    captured_at = parse_timestamp(entry.get("recorded_at")) or fallback_captured_at
    identifiers = extract_identifiers(entry)
    state = entry.get("result") or infer_state(entry)
    return EvidenceRecord(
        file_id=file_id,
        system_id=system_id,
        category=category,
        resource_id=entry.get("resource_id"),
        state=state,
        identifiers=identifiers,
        captured_at=captured_at,
        raw=entry,
    )


def parse_json_file(path: Path, file_id: str) -> List[EvidenceRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records: List[EvidenceRecord] = []

    if "snapshots" in data:
        for snap in data["snapshots"]:
            snap_captured_at = parse_timestamp(snap.get("captured_at"))
            collection = snap.get("collection")
            data_store = snap.get("data_store")
            for rec in snap.get("records", []):
                if collection == "operational_record_history":
                    records.append(_record_from_ledger_entry(file_id, rec, snap_captured_at))
                    continue
                category = classify_category(collection, rec.get("resource_id"))
                system_id = infer_system(category, data_store)
                # A search_service record wraps its actual fields in a
                # nested "document" object (e.g. {"resource_id": "...",
                # "index_name": "...", "document": {"email": ..., ...}}),
                # which extract_identifiers (flat-dict only) would
                # otherwise miss entirely.
                flat = _flatten_one_level(rec)
                identifiers = extract_identifiers(flat)
                state = infer_state(flat)
                records.append(
                    EvidenceRecord(
                        file_id=file_id,
                        system_id=system_id,
                        category=category,
                        resource_id=rec.get("resource_id"),
                        state=state,
                        identifiers=identifiers,
                        captured_at=snap_captured_at,
                        raw=rec,
                    )
                )
        return records

    if "service_exports" in data:
        file_captured_at = parse_timestamp(data.get("exported_at"))
        for system_name, body in data["service_exports"].items():
            for field_name, entries in body.items():
                for entry in entries:
                    flat = _flatten_one_level(entry)
                    nested_docs = None
                    hits = flat.get("hits")
                    if isinstance(hits, dict):
                        nested_docs = hits.get("documents")
                        flat.pop("hits", None)

                    category = classify_category(system_name, field_name)
                    resolved_system = infer_system(category, system_name)
                    captured_at = parse_timestamp(flat.get("observed_at")) or file_captured_at
                    identifiers = extract_identifiers(flat)
                    state = infer_state(flat)
                    records.append(
                        EvidenceRecord(
                            file_id=file_id,
                            system_id=resolved_system,
                            category=category,
                            resource_id=flat.get("resource_id"),
                            state=state,
                            identifiers=identifiers,
                            captured_at=captured_at,
                            raw=flat,
                        )
                    )
                    if nested_docs:
                        for doc in nested_docs:
                            doc_identifiers = extract_identifiers(doc)
                            records.append(
                                EvidenceRecord(
                                    file_id=file_id,
                                    system_id=resolved_system,
                                    category=category,
                                    resource_id=doc.get("resource_id"),
                                    state="FOUND",
                                    identifiers=doc_identifiers,
                                    captured_at=captured_at,
                                    raw=doc,
                                )
                            )
        return records

    if "records" in data and ("origin" in data or "record_count" in data):
        file_captured_at = parse_timestamp(data.get("exported_at"))
        for entry in data["records"]:
            records.append(_record_from_ledger_entry(file_id, entry, file_captured_at))
        return records

    if "records" in data:
        file_captured_at = parse_timestamp(data.get("captured_at"))
        collection = data.get("collection")
        category = classify_category(collection)
        system_id = infer_system(category, data.get("source"))
        for rec in data["records"]:
            identifiers = extract_identifiers(rec)
            state = infer_state(rec)
            records.append(
                EvidenceRecord(
                    file_id=file_id,
                    system_id=system_id,
                    category=category,
                    resource_id=rec.get("resource_id"),
                    state=state,
                    identifiers=identifiers,
                    captured_at=file_captured_at,
                    raw=rec,
                )
            )
        return records

    return records
