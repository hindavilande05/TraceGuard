"""Shared data shapes and format-agnostic helpers used by every evidence parser.

The dataset stores the same underlying facts (which system holds a resource,
what identifiers it still carries, whether a record/process is present,
redacted, anonymized, active, etc.) in many different file shapes (SQL,
JSON, plaintext handover tables, structured logs). Rather than writing a
bespoke identifier extractor per file shape, every parser flattens its
input into plain ``dict`` rows and this module extracts identifiers from
*values* (by pattern) rather than relying on exact, format-specific key
names. This keeps the pipeline robust to the many different column/key
names used for the same concept (``email`` vs ``requester_email`` vs
``owner_email`` vs ``customer_email`` vs ``lookup_key`` ...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

EMAIL_RE = re.compile(r"^[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9\-.]+$")
USER_REF_RE = re.compile(r"^user_\d+$")
HASH_RE = re.compile(r"^[0-9a-f]{32,}$", re.IGNORECASE)

# Known data categories from dataset/deletion_rules.csv, plus DATA_FLOW /
# BACKUP_RESTORE which describe recreation-risk processes rather than a
# retained data category.
KNOWN_CATEGORIES = {
    "USER_PROFILE",
    "SECONDARY_IDENTIFIER",
    "SEARCH_INDEX_ENTRY",
    "CACHE_ENTRY",
    "SUPPORT_MESSAGE",
    "SUPPORT_ATTACHMENT",
    "ANALYTICS_PROFILE",
    "PRODUCT_TELEMETRY",
    "USER_EXPORT",
    "MARKETING_PROFILE",
    "EMAIL_DELIVERY_LOG",
    "SECURITY_AUDIT_LOG",
    "LEGAL_HOLD_RECORD",
    "BACKUP_ARCHIVE",
    "ACCOUNT_TOMBSTONE",
    "DATA_FLOW",
}

# Default system_id for a category when the evidence does not state the
# origin system explicitly.
CATEGORY_TO_SYSTEM = {
    "USER_PROFILE": "account_service",
    "ACCOUNT_TOMBSTONE": "identity_service",
    "SECONDARY_IDENTIFIER": "account_service",
    "SEARCH_INDEX_ENTRY": "search_service",
    "CACHE_ENTRY": "cache_service",
    "SUPPORT_MESSAGE": "support_platform",
    "SUPPORT_ATTACHMENT": "support_platform",
    "ANALYTICS_PROFILE": "analytics_platform",
    "PRODUCT_TELEMETRY": "analytics_platform",
    "USER_EXPORT": "export_service",
    "MARKETING_PROFILE": "marketing_platform",
    "EMAIL_DELIVERY_LOG": "email_delivery_service",
    "SECURITY_AUDIT_LOG": "security_service",
    "LEGAL_HOLD_RECORD": "legal_service",
    "BACKUP_ARCHIVE": "backup_service",
    "DATA_FLOW": "integration_service",
}

# Keyword -> category, checked against table names / collection names /
# log service names / handover-report category column. Order matters:
# more specific keywords are listed first.
_CATEGORY_KEYWORDS = [
    ("tombstone", "ACCOUNT_TOMBSTONE"),
    ("deleted_customer", "ACCOUNT_TOMBSTONE"),
    ("telemetry", "PRODUCT_TELEMETRY"),
    ("analytics", "ANALYTICS_PROFILE"),
    ("search", "SEARCH_INDEX_ENTRY"),
    ("cache", "CACHE_ENTRY"),
    ("attachment", "SUPPORT_ATTACHMENT"),
    ("ticket", "SUPPORT_MESSAGE"),
    ("support", "SUPPORT_MESSAGE"),
    ("conversation", "SUPPORT_MESSAGE"),
    ("export", "USER_EXPORT"),
    ("marketing", "MARKETING_PROFILE"),
    ("email_delivery", "EMAIL_DELIVERY_LOG"),
    ("security_audit", "SECURITY_AUDIT_LOG"),
    ("audit_record", "SECURITY_AUDIT_LOG"),
    ("legal", "LEGAL_HOLD_RECORD"),
    ("backup", "BACKUP_ARCHIVE"),
    ("restore", "BACKUP_ARCHIVE"),
    ("connector", "DATA_FLOW"),
    ("subscription", "DATA_FLOW"),
    ("data_flow", "DATA_FLOW"),
    ("profile", "USER_PROFILE"),
    ("account", "USER_PROFILE"),
]

# Known system identifiers, used to recognize a system name wherever it
# appears (log ``service=`` field, JSON ``system``/``origin_system`` key,
# handover-report system column, etc.).
KNOWN_SYSTEMS = {
    "account_service",
    "search_service",
    "analytics_platform",
    "support_platform",
    "export_service",
    "integration_service",
    "backup_service",
    "security_service",
    "legal_service",
    "cache_service",
    "marketing_platform",
    "email_delivery_service",
    "identity_service",
}


def classify_category(*hints: Optional[str]) -> Optional[str]:
    """Infer a data_category from table/collection/log-event name hints."""
    for hint in hints:
        if not hint:
            continue
        h = hint.lower()
        if h.upper() in KNOWN_CATEGORIES:
            return h.upper()
        for keyword, category in _CATEGORY_KEYWORDS:
            if keyword in h:
                return category
    return None


def infer_system(category: Optional[str], *hints: Optional[str]) -> Optional[str]:
    """Infer a system_id from explicit hints first, else from the category."""
    for hint in hints:
        if not hint:
            continue
        h = hint.lower().strip()
        if h in KNOWN_SYSTEMS:
            return h
    if category:
        return CATEGORY_TO_SYSTEM.get(category)
    return None


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = value.strip()
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def normalize_email(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value.strip().lower()


def normalize_phone(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    # Keep digits and leading + only, so formatting differences
    # ("+33 6 24 34 80 16" vs "+33624348016") do not break matching.
    v = value.strip()
    sign = "+" if v.startswith("+") else ""
    digits = re.sub(r"\D", "", v)
    return sign + digits if digits else None


@dataclass
class Identifiers:
    emails: set = field(default_factory=set)
    phones: set = field(default_factory=set)
    email_hashes: set = field(default_factory=set)
    names: set = field(default_factory=set)
    user_refs: set = field(default_factory=set)

    def is_empty(self) -> bool:
        return not (self.emails or self.phones or self.email_hashes or self.names)

    def merge(self, other: "Identifiers") -> None:
        self.emails |= other.emails
        self.phones |= other.phones
        self.email_hashes |= other.email_hashes
        self.names |= other.names
        self.user_refs |= other.user_refs


_NAME_KEYS = {"name", "full_name", "display_name", "requester_name", "customer_name"}


def extract_identifiers(row: dict) -> Identifiers:
    """Extract identifiers from a flat dict by inspecting key names and
    value shapes, so it works uniformly across SQL rows, JSON records,
    parsed log lines, and parsed handover-report rows."""
    ids = Identifiers()
    for key, value in row.items():
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        sval = str(value).strip()
        if not sval or sval.lower() in ("null", "none"):
            continue
        kl = key.lower()

        if EMAIL_RE.match(sval):
            ids.emails.add(normalize_email(sval))
            continue
        if USER_REF_RE.match(sval):
            ids.user_refs.add(sval)
            continue
        if HASH_RE.match(sval):
            # Matched by value shape (32+ hex chars), not by key name: some
            # evidence formats carry the email hash under an unrelated key
            # name (e.g. a log's "correlation_key"). A checksum/digest
            # field matching this shape by coincidence is harmless here --
            # identifiers are only ever used for exact-value lookups
            # against a specific user's known hash, so a random checksum
            # cannot falsely match unless it IS that exact hash.
            ids.email_hashes.add(sval.lower())
            continue
        if "phone" in kl:
            phone = normalize_phone(sval)
            if phone:
                ids.phones.add(phone)
            continue
        if kl in _NAME_KEYS:
            ids.names.add(sval)
            continue
    return ids


def infer_state(row: dict) -> Optional[str]:
    """Infer an explicit state token from boolean/status-ish fields that
    appear across formats. Falls back to None (caller should default to
    "FOUND", since the row's mere presence in a positive-knowledge
    snapshot implies the record exists) when nothing more specific is
    available."""
    lower_row = {k.lower(): v for k, v in row.items()}

    def as_bool(v):
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes")
        if isinstance(v, int):
            return bool(v)
        return None

    status_code_key = "status_code" if "status_code" in lower_row else ("http_status" if "http_status" in lower_row else None)
    if status_code_key:
        try:
            return "NOT_FOUND" if int(lower_row[status_code_key]) == 404 else "FOUND"
        except (TypeError, ValueError):
            pass
    if "result" in lower_row and lower_row["result"]:
        r = str(lower_row["result"]).strip().upper()
        if r == "HIT":
            return "FOUND"
        if r == "MISS":
            return "NOT_FOUND"
        return r
    if "account_status" in lower_row and lower_row["account_status"]:
        return str(lower_row["account_status"]).strip().upper()
    if "enabled" in lower_row:
        b = as_bool(lower_row["enabled"])
        if b is not None:
            return "ACTIVE" if b else "DISABLED"
    if "restore_allowed" in lower_row:
        b = as_bool(lower_row["restore_allowed"])
        if b is not None:
            return "ACTIVE" if b else "BLOCKED"
    if "eligible" in lower_row and "deletion_block" in lower_row:
        eligible = as_bool(lower_row["eligible"])
        blocked = as_bool(lower_row["deletion_block"])
        if eligible is not None and blocked is not None:
            return "ACTIVE" if (eligible and not blocked) else "BLOCKED"
    if "download_available" in lower_row:
        b = as_bool(lower_row["download_available"])
        if b is not None:
            return "FOUND" if b else "NOT_FOUND"
    if "rows" in lower_row:
        try:
            return "FOUND" if int(lower_row["rows"]) > 0 else "NOT_FOUND"
        except (TypeError, ValueError):
            pass
    return None


@dataclass
class SnapshotScope:
    """Describes what a single SQL table (or other roster-shaped evidence)
    claims to cover, so absence of a resource from it can be judged as
    meaningful (a complete roster that should have listed the resource)
    or not (a small/partial sample that proves nothing by omission).

    ``is_replica_comparison`` marks tables that are explicitly framed by
    the evidence file as "compare primary vs replica for drift" -- the
    strongest case for treating the table as an authoritative roster of
    what each store currently holds.
    """

    file_id: str
    table_name: str
    system_id: Optional[str]
    category: Optional[str]
    captured_at: Optional[datetime]
    resource_ids: frozenset
    is_replica_comparison: bool = False

    @property
    def is_primary(self) -> bool:
        return "primary" in self.table_name.lower()


@dataclass
class EvidenceRecord:
    """One normalized fact extracted from a single evidence file."""

    file_id: str
    system_id: Optional[str]
    category: Optional[str]
    resource_id: Optional[str]
    state: Optional[str]
    identifiers: Identifiers
    captured_at: Optional[datetime]
    raw: dict = field(default_factory=dict)

    def all_identifier_values(self):
        yield from self.identifiers.emails
        yield from self.identifiers.phones
        yield from self.identifiers.email_hashes
        yield from self.identifiers.user_refs
