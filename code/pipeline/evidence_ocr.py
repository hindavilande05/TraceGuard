"""OCR-based extraction for screenshot (.png) and scanned/rendered (.pdf)
evidence. Uses a local OCR pipeline only (pytesseract + PyMuPDF), no cloud
vision API and no additional vision LLM, per the approved architecture.

Raw OCR text is cached to disk (code/.cache/ocr/<file_id>.txt) so repeated
runs don't re-OCR the same file, and so a human can inspect exactly what
the OCR engine saw for debugging. That cache is local to this machine and
excluded from version control; nothing from it is printed to stdout or
written to log.txt verbatim (which could otherwise leak the personal data
these evidence files legitimately contain) -- callers only see the
structured facts extracted from it.

These admin-console screenshots and documents are each about a single
resource/record at a time (never an exhaustive roster), so records
produced here are always treated as point-lookup evidence -- they never
feed SnapshotScope / absence-by-omission reasoning (see provenance.py).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from .common import EvidenceRecord, Identifiers, classify_category

#  Tesseract has been observed to render an underscore as a space
# non-deterministically on the same source image across runs (confirmed:
# "data_flow_9846" -> "data_flow 9846" in one real OCR pass), so every
# underscore in these patterns is matched as underscore-or-space.
_RESOURCE_ID_RE = re.compile(
    r"\b(account|search|analytics|support|export|backup|data[ _]flow|audit[ _]record|"
    r"tombstone|legal[ _]hold|cache|marketing)[ _][0-9]{2,6}\b",
    re.IGNORECASE,
)
_USER_REF_RE = re.compile(r"\buser[ _][0-9]{1,6}\b", re.IGNORECASE)


def _normalize_token(s: str) -> str:
    """Canonical resource_id/user_ref form: lowercase, space-vs-underscore
    OCR noise collapsed to underscore (the real participant file naming
    convention always uses underscores; a space here is an OCR artifact,
    not a genuinely different id)."""
    return re.sub(r"\s+", "_", s.strip().lower())
# Legal/finance retention registers (e.g. legal_retention_register_*.pdf)
# reference the retained record by order number, not a "legal_hold_NNN"
# resource id -- only trust ORD- numbers as LEGAL_HOLD_RECORD resources
# when the page is unambiguously a retention register (an ORD- number
# shows up in many ordinary, irrelevant contexts otherwise, e.g. a
# customer's order history screenshot).
_ORDER_REF_RE = re.compile(r"\bORD-[0-9]{4,}\b")
_RETENTION_REGISTER_RE = re.compile(r"retain[- ]until|retention register|disputed orders", re.IGNORECASE)
# Last-resort fallback resource id for a document that carries clear
# identifiers (so it IS evidentiary) but no recognized system-prefixed
# resource id -- e.g. a customer statement referenced only by
# "Statement NST-2026-0718-1842", not an "account_NNN" token.
_GENERIC_DOCUMENT_REF_RE = re.compile(r"\b[A-Z]{2,5}-[0-9]{4}-?[0-9A-Z-]{2,}\b")

_RESOURCE_PREFIX_TO_CATEGORY = {
    "account": "USER_PROFILE",
    "search": "SEARCH_INDEX_ENTRY",
    "analytics": "ANALYTICS_PROFILE",
    "support": "SUPPORT_MESSAGE",
    "export": "USER_EXPORT",
    "backup": "BACKUP_ARCHIVE",
    "data_flow": "DATA_FLOW",
    "audit_record": "SECURITY_AUDIT_LOG",
    "tombstone": "ACCOUNT_TOMBSTONE",
    "legal_hold": "LEGAL_HOLD_RECORD",
    "cache": "CACHE_ENTRY",
    "marketing": "MARKETING_PROFILE",
}
_RESOURCE_PREFIX_TO_SYSTEM = {
    "account": "account_service",
    "search": "search_service",
    "analytics": "analytics_platform",
    "support": "support_platform",
    "export": "export_service",
    "backup": "backup_service",
    "data_flow": "integration_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "legal_hold": "legal_service",
    "cache": "cache_service",
    "marketing": "marketing_platform",
}

# Phrases observed in the admin-console screenshots that indicate a
# lifecycle state in plain UI language, mapped to the same state
# vocabulary used by every other evidence format.
_STATE_PHRASES = [
    (re.compile(r"retained reference", re.IGNORECASE), "REDACTED"),
    (re.compile(r"continuous sync", re.IGNORECASE), "ACTIVE"),
    (re.compile(r"\bcontinuous\b", re.IGNORECASE), "ACTIVE"),
    (re.compile(r"\bpaused\b", re.IGNORECASE), "DISABLED"),
    (re.compile(r"\bdisabled\b", re.IGNORECASE), "DISABLED"),
    (re.compile(r"restore blocked", re.IGNORECASE), "BLOCKED"),
    (re.compile(r"restore allowed", re.IGNORECASE), "ACTIVE"),
    (re.compile(r"\bnot found\b", re.IGNORECASE), "NOT_FOUND"),
    (re.compile(r"\banonymi[sz]ed\b", re.IGNORECASE), "ANONYMIZED"),
    (re.compile(r"\bredacted\b", re.IGNORECASE), "REDACTED"),
]


def _infer_state_from_text(text: str) -> Optional[str]:
    for pattern, state in _STATE_PHRASES:
        if pattern.search(text):
            return state
    return None


def _ocr_image_bytes(image) -> str:
    import pytesseract

    # PSM 11 ("sparse text, no particular order") was verified against the
    # real evidence screenshots to outperform the default PSM 3: these
    # admin-console UIs are dense multi-column layouts (nav + table +
    # detail panel), and PSM 3's column-assuming segmentation produced at
    # least one confirmed character misread (an email's "1" read as "i")
    # that PSM 11 reads correctly.
    return pytesseract.image_to_string(image, config="--psm 11")


def _get_cache_path(file_id: str) -> Path:
    from . import config

    config.OCR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.OCR_CACHE_DIR / f"{file_id}.txt"


def extract_raw_text(path: Path, file_id: str) -> str:
    """OCR a .png/.jpg/.pdf file to plain text, using a disk cache keyed
    by file_id so repeated pipeline runs don't re-OCR unchanged files."""
    cache_path = _get_cache_path(file_id)
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    ext = path.suffix.lower()
    text = ""
    if ext in (".png", ".jpg", ".jpeg"):
        from PIL import Image

        with Image.open(path) as img:
            text = _ocr_image_bytes(img)
    elif ext == ".pdf":
        import pymupdf as fitz

        parts = []
        with fitz.open(path) as doc:
            for page in doc:
                direct_text = page.get_text().strip()
                if len(direct_text) > 40:
                    # Already-text PDF page; no need to rasterize + OCR.
                    parts.append(direct_text)
                    continue
                pix = page.get_pixmap(dpi=300)
                from PIL import Image
                import io

                img = Image.open(io.BytesIO(pix.tobytes("png")))
                parts.append(_ocr_image_bytes(img))
        text = "\n".join(parts)

    cache_path.write_text(text, encoding="utf-8")
    return text


def parse_ocr_evidence(path: Path, file_id: str) -> List[EvidenceRecord]:
    """Produce EvidenceRecord(s) from an OCR'd image/PDF.

    One record is emitted per distinct resource-id-like token found on the
    page. When the page names exactly one resource, every identifier found
    anywhere on the page is attributed to it (handles a detail panel whose
    labels/values sit far from the resource's own heading in OCR reading
    order). When the page lists multiple distinct resources (a table with
    one row per customer), identifiers are instead windowed to the
    nearest-preceding resource token in reading order, so different rows'
    customers are not cross-contaminated (see _window_identifiers_by_resource).
    captured_at falls back to the evidence_files.csv value (set by the
    caller) when no on-screen date can be parsed.
    """
    text = extract_raw_text(path, file_id)
    if not text.strip():
        return []

    if _RETENTION_REGISTER_RE.search(text) and _ORDER_REF_RE.search(text):
        register_records = _parse_retention_register_rows(text, file_id)
        if register_records:
            return register_records

    # extract_identifiers() (common.py) matches a value against a whole
    # dict value, which doesn't fit free-flowing OCR text; scan token
    # patterns directly instead.
    page_identifiers = _extract_identifiers_from_free_text(text)

    resource_tokens: dict = {}
    for m in _RESOURCE_ID_RE.finditer(text):
        resource_tokens[_normalize_token(m.group(0))] = _normalize_token(m.group(1))
    user_refs = {_normalize_token(m.group(0)) for m in _USER_REF_RE.finditer(text)}
    page_identifiers.user_refs |= user_refs

    state = _infer_state_from_text(text)
    # captured_at is intentionally left None here: an on-screen date in a
    # screenshot (e.g. a customer's "Last contact" time, in a local
    # timezone) is a business fact, not the capture time of the artifact
    # itself. The caller (evidence_loader.py) falls back to the
    # authoritative captured_at from evidence_files.csv for every record
    # that doesn't set its own.
    captured_at = None

    has_any_identifier = bool(
        page_identifiers.emails or page_identifiers.phones or page_identifiers.email_hashes or page_identifiers.user_refs
    )

    if not resource_tokens:
        # A "Customer Reference Registry" / tombstone view keys itself by
        # the stable user_id directly (no separate "tombstone_NNN" token
        # ever appears on the page) -- use the user_ref itself as the
        # resource id for the ACCOUNT_TOMBSTONE record in that case.
        if state == "REDACTED" and page_identifiers.user_refs:
            user_ref = sorted(page_identifiers.user_refs)[0]
            return [
                EvidenceRecord(
                    file_id=file_id,
                    system_id="identity_service",
                    category="ACCOUNT_TOMBSTONE",
                    resource_id=user_ref,
                    state=state,
                    identifiers=page_identifiers,
                    captured_at=captured_at,
                    raw={"ocr_text_len": len(text), "fallback": "tombstone_by_user_ref"},
                )
            ]
        # No recognized system-prefixed resource id, but the page may
        # still be evidentiary (e.g. a customer statement PDF showing a
        # still-identifiable name/email). Fall back to a generic document
        # reference number rather than silently discarding the page.
        if has_any_identifier:
            fallback_ref = _GENERIC_DOCUMENT_REF_RE.search(text)
            if fallback_ref:
                return [
                    EvidenceRecord(
                        file_id=file_id,
                        system_id="account_service",
                        category="USER_PROFILE",
                        resource_id=fallback_ref.group(0),
                        state=state or "FOUND",
                        identifiers=page_identifiers,
                        captured_at=captured_at,
                        raw={"ocr_text_len": len(text), "fallback": True},
                    )
                ]
        return []

    distinct_tokens = set(resource_tokens)
    per_resource_identifiers = (
        _window_identifiers_by_resource(text, resource_tokens) if len(distinct_tokens) > 1 else None
    )

    records = []
    for token, prefix in resource_tokens.items():
        category = classify_category(path.stem, prefix) or _RESOURCE_PREFIX_TO_CATEGORY.get(prefix)
        system_id = _RESOURCE_PREFIX_TO_SYSTEM.get(prefix)
        identifiers = per_resource_identifiers[token] if per_resource_identifiers else page_identifiers
        records.append(
            EvidenceRecord(
                file_id=file_id,
                system_id=system_id,
                category=category,
                resource_id=token,
                state=state,
                identifiers=identifiers,
                captured_at=captured_at,
                raw={"ocr_text_len": len(text)},
            )
        )
    return records


def _window_identifiers_by_resource(text: str, resource_tokens: dict) -> dict:
    """When a page lists MULTIPLE distinct resources (e.g. a connector
    table with one row per customer), sharing every identifier found
    anywhere on the page across every resource would cross-contaminate
    different users' data (confirmed against a real page:
    connector_management_console_20260807.png lists both data_flow_1121
    for one customer and data_flow_9846 for a different one). Instead,
    attribute each identifier to the most recently seen resource token in
    reading order -- OCR (PSM 11) preserves line order well enough for
    this to correctly separate same-page table rows, even though it can't
    perfectly place a detail panel that echoes the page's selected row far
    from that row's own table line (an accepted, documented limitation)."""
    buckets = {token: Identifiers() for token in resource_tokens}
    # Header/title text often precedes the first resource token's own line
    # (e.g. OCR splits "user_11 . account_5762" across two lines) -- treat
    # anything before the first resource mention as belonging to that
    # first resource rather than dropping it.
    current = next(iter(resource_tokens), None)
    for line in text.splitlines():
        line_normalized = _normalize_token(line)
        matched_token = next((t for t in resource_tokens if t in line_normalized), None)
        if matched_token:
            current = matched_token
        if current:
            buckets[current].merge(_extract_identifiers_from_free_text(line))
            for m in _USER_REF_RE.finditer(line):
                buckets[current].user_refs.add(_normalize_token(m.group(0)))
    return buckets


def _parse_retention_register_rows(text: str, file_id: str) -> List[EvidenceRecord]:
    """Each row of a disputed-orders/tax-records retention register pairs
    one order reference with one user_id -- a multi-subject document, so
    (unlike the single-customer admin-console screenshots) each row must
    become its own record with only that row's own identifier, not the
    whole page's identifiers."""
    records = []
    for line in text.splitlines():
        order_match = _ORDER_REF_RE.search(line)
        user_match = _USER_REF_RE.search(line)
        if not (order_match and user_match):
            continue
        records.append(
            EvidenceRecord(
                file_id=file_id,
                system_id="legal_service",
                category="LEGAL_HOLD_RECORD",
                resource_id=order_match.group(0),
                state="FOUND",
                identifiers=Identifiers(user_refs={user_match.group(0).lower()}),
                captured_at=None,
                raw={"line": line.strip()},
            )
        )
    return records


_EMAIL_TOKEN_RE = re.compile(r"[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9\-.]+")
_PHONE_TOKEN_RE = re.compile(r"\+\d[\d\s\-]{6,}\d")
_HASH_TOKEN_RE = re.compile(r"\b[0-9a-f]{32,}\b", re.IGNORECASE)


def _extract_identifiers_from_free_text(text: str) -> Identifiers:
    from .common import normalize_email, normalize_phone

    ids = Identifiers()
    for m in _EMAIL_TOKEN_RE.finditer(text):
        ids.emails.add(normalize_email(m.group(0)))
    for m in _PHONE_TOKEN_RE.finditer(text):
        phone = normalize_phone(m.group(0))
        if phone:
            ids.phones.add(phone)
    for m in _HASH_TOKEN_RE.finditer(text):
        ids.email_hashes.add(m.group(0).lower())
    return ids
