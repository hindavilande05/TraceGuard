"""Parse .sql snapshot-export evidence files (SQLite-compatible dumps).

Table shapes vary across files (per-category tables with direct columns in
"inventory" captures, vs a generic resource_id/origin_system/collection_name
/record_json shape in replica-comparison captures), so this parser loads
each file into an in-memory SQLite database, reads every table generically
via PRAGMA table_info, and normalizes whatever columns are present through
the shared helpers in common.py.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import List, Optional

from .common import (
    EvidenceRecord,
    SnapshotScope,
    classify_category,
    extract_identifiers,
    infer_state,
    infer_system,
    parse_timestamp,
)

_CAPTURED_AT_RE = re.compile(r"--\s*Captured at:\s*(\S+)", re.IGNORECASE)
_TOPOLOGY_RE = re.compile(r"--\s*Database topology:\s*(\S+)", re.IGNORECASE)


def _file_level_captured_at(sql_text: str):
    m = _CAPTURED_AT_RE.search(sql_text)
    if m:
        return parse_timestamp(m.group(1))
    return None


def parse_sql_file(path: Path, file_id: str):
    """Returns (records, snapshot_scopes).

    ``snapshot_scopes`` describes, per table, the full set of resource_ids
    that table lists at its capture time -- used later to judge whether a
    resource's absence from a table is meaningful (see provenance.py).
    """
    sql_text = path.read_text(encoding="utf-8")
    file_captured_at = _file_level_captured_at(sql_text)
    topology_match = _TOPOLOGY_RE.search(sql_text)
    is_replica_comparison = bool(topology_match) and "Compare the primary and any replica" in sql_text

    conn = sqlite3.connect(":memory:")
    conn.executescript(sql_text)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # snapshot_scope(origin_system, captured_at) gives a per-system capture
    # time in some files; use it as a fallback more specific than the
    # file-level comment.
    per_system_captured_at = {}
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]

    if "snapshot_scope" in tables:
        for row in cur.execute("SELECT * FROM snapshot_scope").fetchall():
            d = dict(row)
            system = d.get("origin_system")
            ts = parse_timestamp(d.get("captured_at"))
            if system and ts:
                per_system_captured_at[system] = ts

    records: List[EvidenceRecord] = []
    # A single table can span multiple (system_id, category) combinations
    # when it uses the generic record_json wrapper shape (each row's own
    # origin_system/collection_name can differ), so the resource-id roster
    # for scope purposes is grouped per (table, system_id, category), not
    # just per table.
    scope_groups: dict = {}
    # Tables using the generic resource_id/origin_system/record_json shape
    # can legitimately span several (system, category) combinations, and a
    # sibling "primary" vs "replica" table pair is meant to be compared
    # key-for-key. If one sibling has zero rows for a (system, category)
    # that another sibling *does* have, that's a meaningful empty roster
    # (and often the drift itself), not "this table doesn't cover that
    # category" -- tracked here so it can be backfilled after the main loop.
    generic_tables: set = set()

    for table in tables:
        if table == "snapshot_scope":
            continue

        for row in cur.execute(f"SELECT * FROM {table}").fetchall():
            flat = dict(row)

            # Generic replica-comparison shape wraps the real record as a
            # JSON blob; merge it into the flat row so downstream helpers
            # see the actual identifier fields.
            record_json = flat.pop("record_json", None)
            if record_json:
                generic_tables.add(table)
                try:
                    nested = json.loads(record_json)
                    if isinstance(nested, dict):
                        for k, v in nested.items():
                            flat.setdefault(k, v)
                except (TypeError, ValueError):
                    pass

            resource_id = flat.get("resource_id")
            category = classify_category(
                table, flat.get("record_type"), flat.get("collection_name"), resource_id
            )
            system_id = infer_system(
                category, flat.get("origin_system"), flat.get("system"), table
            )
            identifiers = extract_identifiers(flat)
            state = infer_state(flat)

            captured_at: Optional[object] = None
            for ts_key in ("updated_at", "indexed_at", "recorded_at", "observed_at"):
                if flat.get(ts_key):
                    captured_at = parse_timestamp(flat[ts_key])
                    break
            if captured_at is None and system_id in per_system_captured_at:
                captured_at = per_system_captured_at[system_id]
            if captured_at is None:
                captured_at = file_captured_at

            records.append(
                EvidenceRecord(
                    file_id=file_id,
                    system_id=system_id,
                    category=category,
                    resource_id=resource_id,
                    state=state,
                    identifiers=identifiers,
                    captured_at=captured_at,
                    raw={"table": table, **flat},
                )
            )

            if resource_id:
                key = (table, system_id, category)
                group = scope_groups.setdefault(
                    key, {"resource_ids": set(), "captured_at": captured_at}
                )
                group["resource_ids"].add(resource_id)
                # Keep the latest captured_at seen for this group as the
                # scope's representative capture time.
                if captured_at and (group["captured_at"] is None or captured_at > group["captured_at"]):
                    group["captured_at"] = captured_at

    if generic_tables:
        all_keys = {
            (system_id, category)
            for (table, system_id, category) in scope_groups
            if table in generic_tables
        }
        for table in generic_tables:
            for system_id, category in all_keys:
                key = (table, system_id, category)
                if key not in scope_groups:
                    scope_groups[key] = {
                        "resource_ids": set(),
                        "captured_at": per_system_captured_at.get(system_id, file_captured_at),
                    }

    scopes: List[SnapshotScope] = [
        SnapshotScope(
            file_id=file_id,
            table_name=table,
            system_id=system_id,
            category=category,
            captured_at=group["captured_at"],
            resource_ids=frozenset(group["resource_ids"]),
            is_replica_comparison=is_replica_comparison,
        )
        for (table, system_id, category), group in scope_groups.items()
    ]

    conn.close()
    return records, scopes
