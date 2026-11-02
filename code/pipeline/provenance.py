"""Resource-ownership resolution and absence-by-omission reasoning, with
explicit provenance, confidence, and temporal-safety guarantees.

Two things this module deliberately does NOT do, by design:

1. It never claims a resource belongs to a user just because *some*
   identifier value appears *somewhere* in the evidence for that
   resource. Every match records which identifier matched (email/phone/
   hash/user_id vs. a bare name), the evidence file and timestamp it came
   from, and a confidence tier -- because the dataset contains deliberate
   near-duplicate-name decoys (e.g. "Jonas Berg" / "Jonas Berglund" /
   "Joana Berg" in evidence/databases/customer_search_primary_20260910.sql)
   where matching on name alone would misattribute a resource.

2. It never looks at evidence captured after the case's audit_at. Every
   lookup here takes an explicit ``as_of`` cutoff and filters to
   ``captured_at <= as_of`` before doing anything else. There is no
   "future evidence" code path in this module at all -- the dataset
   contract (AGENTS.md ssec 6.6) does not carve out an exception for
   recreation-risk findings, so none is implemented here; a recreation
   risk is only reported when evidence *as of audit_at* already shows the
   flow/backup active, not by peeking ahead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from .common import EvidenceRecord, Identifiers, SnapshotScope
from .evidence_loader import EvidenceStore

STRONG_PRESENCE_STATES = {"FOUND", "ACTIVE", "IDENTIFIABLE", "HIT"}

# Every user who resolves to a user_id definitionally has (or had) exactly
# one account/profile record -- that is what "being a user" means in this
# dataset, independent of whether any surviving evidence happens to show
# it. A support ticket, analytics profile, export, backup entry, etc. are
# each conditional on the user's behavior and are NOT assumed this way;
# those categories still require genuine independent evidence of prior
# existence (see established_presence) before an omission can be trusted
# as proof of deletion.
CATEGORIES_IMPLIED_BY_USER_EXISTENCE = {"USER_PROFILE", "ACCOUNT_TOMBSTONE"}


@dataclass
class ResourceMatch:
    """One piece of evidence linking a resource_id to a user, through a
    specific identifier value, with full provenance."""

    resource_id: str
    system_id: Optional[str]
    category: Optional[str]
    matched_identifier_type: str  # "email" | "phone" | "email_hash" | "user_ref"
    matched_identifier_value: str
    confidence: str  # "high" (strong identifier) -- see note below
    file_id: str
    captured_at: Optional[datetime]


@dataclass
class AbsenceCheck:
    status: str  # "ABSENT_CONFIRMED" | "STILL_PRESENT" | "INCONCLUSIVE"
    file_id: Optional[str]
    captured_at: Optional[datetime]
    reason: str


def _as_of(records: List[EvidenceRecord], as_of: datetime) -> List[EvidenceRecord]:
    return [r for r in records if r.captured_at is not None and r.captured_at <= as_of]


class ProvenanceEngine:
    def __init__(self, store: EvidenceStore):
        self.store = store

    # ------------------------------------------------------------------
    # Resource ownership, with provenance + confidence (point 3)
    # ------------------------------------------------------------------
    def find_resources_for_user(self, identifiers: Identifiers, as_of: datetime) -> List[ResourceMatch]:
        """All resources linked to this user's identifiers via a *strong*
        identifier (email, phone, email_hash, or stable user_id/user_ref),
        evidenced at or before ``as_of``. Bare-name matches are
        deliberately excluded here: the dataset contains near-duplicate
        names across different users, so a name match alone is not
        attributed as ownership (see module docstring).
        """
        typed_values = (
            [("email", v) for v in identifiers.emails]
            + [("phone", v) for v in identifiers.phones]
            + [("email_hash", v) for v in identifiers.email_hashes]
            + [("user_ref", v) for v in identifiers.user_refs]
        )

        matches: List[ResourceMatch] = []
        for id_type, value in typed_values:
            for rec in self.store.by_identifier.get(value, []):
                if rec.captured_at is None or rec.captured_at > as_of:
                    continue
                if not rec.resource_id:
                    continue
                matches.append(
                    ResourceMatch(
                        resource_id=rec.resource_id,
                        system_id=rec.system_id,
                        category=rec.category,
                        matched_identifier_type=id_type,
                        matched_identifier_value=value,
                        confidence="high",
                        file_id=rec.file_id,
                        captured_at=rec.captured_at,
                    )
                )
        return matches

    def resources_by_category(
        self, identifiers: Identifiers, as_of: datetime
    ) -> Dict[tuple, List[ResourceMatch]]:
        """Group find_resources_for_user() results by (system_id, category,
        resource_id) so callers can see every distinct resource a user is
        linked to, with all supporting provenance kept per resource."""
        grouped: Dict[tuple, List[ResourceMatch]] = {}
        for m in self.find_resources_for_user(identifiers, as_of):
            key = (m.system_id, m.category, m.resource_id)
            grouped.setdefault(key, []).append(m)
        return grouped

    # ------------------------------------------------------------------
    # Direct, at-or-before-audit_at evidence for one resource (point 5)
    # ------------------------------------------------------------------
    def latest_direct_evidence(self, resource_id: str, as_of: datetime) -> Optional[EvidenceRecord]:
        """The most recent evidence record that explicitly names this
        resource_id, at or before ``as_of``. None if no such record
        exists (not the same as "absent" -- see check_absence below)."""
        candidates = _as_of(self.store.by_resource.get(resource_id, []), as_of)
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.captured_at)

    def established_presence(
        self, resource_id: str, system_id: Optional[str], category: Optional[str], as_of: datetime
    ) -> Optional[EvidenceRecord]:
        """Earliest at-or-before-``as_of`` evidence showing this resource
        existing (with identifiers still attached, or an explicit
        FOUND/ACTIVE/IDENTIFIABLE state) -- i.e. independent confirmation
        that the resource *should* be present absent deletion. Required
        before absence-by-omission is accepted as proof (point 4)."""
        candidates = _as_of(self.store.by_resource.get(resource_id, []), as_of)
        presence = [
            r
            for r in candidates
            if (system_id is None or r.system_id == system_id)
            and (category is None or r.category == category)
            and (not r.identifiers.is_empty() or (r.state and r.state.upper() in STRONG_PRESENCE_STATES))
        ]
        if not presence:
            return None
        return min(presence, key=lambda r: r.captured_at)

    # ------------------------------------------------------------------
    # Absence-by-omission, only when demonstrably safe (point 4)
    # ------------------------------------------------------------------
    def _pick_authoritative_scope(self, scopes: List[SnapshotScope]) -> SnapshotScope:
        """When several same-timestamp scopes exist for one (system,
        category) -- e.g. a primary table plus one or more replica tables
        in the same replication-drift-comparison file -- the *primary*
        table (when the file distinguishes one) is the authoritative
        source for "has this been deleted". A replica or failover table
        disagreeing with primary is drift/restore risk, not proof of the
        deletion's own state -- see find_drift(). Ties are broken
        deterministically (latest captured_at, then primary-first, then
        table name) rather than by incidental dict/list ordering.
        """
        return sorted(
            scopes,
            key=lambda s: (s.captured_at, s.is_primary, s.table_name),
            reverse=True,
        )[0]

    def check_absence_by_snapshot(
        self,
        resource_id: str,
        system_id: Optional[str],
        category: Optional[str],
        as_of: datetime,
        user_resolved: bool = False,
    ) -> AbsenceCheck:
        """Judge whether a resource's absence from the most recent
        matching snapshot roster is trustworthy proof of deletion.

        Rules (per explicit review feedback):
          - Only SQL table rosters count as "snapshot scope" (see
            evidence_sql.py / SnapshotScope) -- a single log line, OCR'd
            document, or point-lookup JSON record never counts as an
            exhaustive roster, so its absence proves nothing by itself.
          - The chosen snapshot must be captured at or before ``as_of``.
          - When a primary table and one or more replica/failover tables
            tie on captured_at, the primary table is authoritative for
            this check; a replica still holding the resource is reported
            separately as drift (see find_drift).
          - Independent prior evidence (``established_presence``) must
            show the resource existed in this same system/category before
            concluding its absence now means deletion. Without that,
            the result is INCONCLUSIVE, not ABSENT -- *except* for
            USER_PROFILE / ACCOUNT_TOMBSTONE when ``user_resolved=True``:
            every resolved user definitionally has exactly one account
            record, so that one category does not need separate evidence
            of prior existence. This is the only such exception, applied
            explicitly here rather than silently; every other category
            still requires genuine evidence.
        """
        scopes = [
            s
            for s in self.store.snapshots_by_scope.get((system_id, category), [])
            if s.captured_at is not None and s.captured_at <= as_of
        ]
        if not scopes:
            return AbsenceCheck(
                "INCONCLUSIVE",
                None,
                None,
                f"no snapshot roster available for ({system_id}, {category}) at or before {as_of.isoformat()}",
            )

        latest_ts = max(s.captured_at for s in scopes)
        latest_scope = self._pick_authoritative_scope([s for s in scopes if s.captured_at == latest_ts])

        if resource_id in latest_scope.resource_ids:
            return AbsenceCheck(
                "STILL_PRESENT",
                latest_scope.file_id,
                latest_scope.captured_at,
                "resource is still listed in the latest matching snapshot roster",
            )

        established = self.established_presence(resource_id, system_id, category, as_of)
        kind = "replica-comparison" if latest_scope.is_replica_comparison else "inventory"

        if established is not None:
            return AbsenceCheck(
                "ABSENT_CONFIRMED",
                latest_scope.file_id,
                latest_scope.captured_at,
                f"resource absent from the latest matching {kind} snapshot roster "
                f"({latest_scope.file_id} @ {latest_scope.captured_at.isoformat()}); prior existence "
                f"established by {established.file_id} @ {established.captured_at.isoformat()}",
            )

        if user_resolved and category in CATEGORIES_IMPLIED_BY_USER_EXISTENCE:
            return AbsenceCheck(
                "ABSENT_CONFIRMED",
                latest_scope.file_id,
                latest_scope.captured_at,
                f"resource absent from the latest matching {kind} snapshot roster "
                f"({latest_scope.file_id} @ {latest_scope.captured_at.isoformat()}); no direct prior "
                f"evidence survives, but the request's own subject is a resolved user, who "
                f"definitionally has exactly one {category} record (explicit exception, not a "
                f"general omission-is-proof rule)",
            )

        return AbsenceCheck(
            "INCONCLUSIVE",
            latest_scope.file_id,
            latest_scope.captured_at,
            "resource absent from the latest matching snapshot, but no independent prior "
            "evidence establishes it ever existed in this system/category -- omission alone "
            "is not treated as proof of deletion",
        )

    def find_drift(
        self, resource_id: str, system_id: Optional[str], category: Optional[str], as_of: datetime
    ) -> List[SnapshotScope]:
        """Non-primary (replica/failover/reporting/read) snapshot scopes,
        at or before ``as_of``, that still list this resource even though
        it is absent from the authoritative primary scope at the same
        point in time. A non-empty result is restore/recreation-risk
        evidence (the resource can still be copied back from that store),
        distinct from whether the primary record itself was deleted."""
        scopes = [
            s
            for s in self.store.snapshots_by_scope.get((system_id, category), [])
            if s.captured_at is not None and s.captured_at <= as_of
        ]
        if not scopes:
            return []
        latest_ts = max(s.captured_at for s in scopes)
        latest = [s for s in scopes if s.captured_at == latest_ts]
        return [
            s
            for s in latest
            if not s.is_primary and resource_id in s.resource_ids
        ]
