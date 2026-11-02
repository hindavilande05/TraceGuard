"""Parse plaintext evidence: the shift-handover report (.txt) and
structured application logs (.log).

Handover report lines look like::

    2026-10-01T12:00:00Z | account_service | account_146 | USER_PROFILE | NOT_FOUND | e.lindberg18@yahoo.com

Log lines are space-separated ``key=value`` tokens (values may be quoted),
e.g.::

    2026-09-15T11:51:08Z level=WARN service=support_platform ... resource_id=support_9553 event=message_pii_scan matched_value="e.varga90@yahoo.com" detector=direct_identifier

Only "completed"/terminal-looking events that carry both a resource_id and
an identifier are turned into evidence records; purely diagnostic lines
(retries, cache refreshes, started-but-not-completed events) are skipped.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import List, Optional

from .common import EvidenceRecord, classify_category, extract_identifiers, infer_state, infer_system, parse_timestamp

_TOKEN_RE = re.compile(r'(\S+?)=("(?:[^"\\]|\\.)*"|\S+)')
# "[LEVEL] [service] [trace_id] event_name: key=\"val\" ..." -- the event
# name appears as a bare word before a colon, not as "event=...".
_BRACKETED_RE = re.compile(r"^\[(\w+)\]\s*\[([\w.-]+)\]\s*\[([\w.-]+)\]\s*(\S+):\s*(.*)$")
# RFC5424-ish syslog: "<PRI>VERSION hostname app-name procid - - key=val ...".
# procid here IS this line's trace/correlation id, but as a bare positional
# token, not "trace_id=...".
_SYSLOG_RE = re.compile(r"^<\d+>\d+\s+\S+\s+(\S+)\s+(\S+)\s+-\s+-\s+(.*)$")


def _flatten_json_log_value(d: dict) -> dict:
    """A JSON-structured log line's "service"/"event" fields line up with
    the key=value format's same-named fields, so once parsed as JSON the
    rest of the pipeline (classify_category/extract_identifiers/
    infer_state) needs no special-casing."""
    flat = {}
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            continue
        flat[k] = v
    return flat


def _extract_tokens(text: str) -> dict:
    result = {}
    for key, value in _TOKEN_RE.findall(text):
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        result[key] = value
    return result


def _parse_log_line(line: str) -> dict:
    """Dispatches across the several distinct log-line shapes actually
    present in dataset/evidence/logs/ (confirmed by inspecting all 26
    files directly, not assumed): plain key=value, JSON-per-line,
    "[LEVEL] [service] [trace] event_name: key=val" bracketed, pipe-
    delimited "TS | LEVEL | service | trace | event | key=val", an
    RFC5424-ish syslog header ("<15>1 node-8 service tr-X - - key=val",
    where tr-X is a bare positional trace id), and a
    "message=\"<escaped nested key=val pairs>\"" wrapper.
    """
    parts = line.split(None, 1)
    if not parts:
        return {}
    result = {"_timestamp": parts[0]}
    if len(parts) < 2:
        return result

    rest = parts[1].strip()
    if rest.startswith("{"):
        try:
            parsed = json.loads(rest)
            if isinstance(parsed, dict):
                result.update(_flatten_json_log_value(parsed))
                return result
        except (TypeError, ValueError):
            pass

    bracket_match = _BRACKETED_RE.match(rest)
    if bracket_match:
        level, service, trace_id, event, remainder = bracket_match.groups()
        result.update({"level": level, "service": service, "trace_id": trace_id, "event": event})
        result.update(_extract_tokens(remainder))
        return result

    syslog_match = _SYSLOG_RE.match(rest)
    if syslog_match:
        service, trace_id, remainder = syslog_match.groups()
        result.update({"service": service, "trace_id": trace_id})
        result.update(_extract_tokens(remainder))
        return result

    if rest.count("|") >= 4:
        segments = [s.strip() for s in rest.split("|")]
        # Expect: '', LEVEL, service, trace_id, event, [key=val tail]
        if len(segments) >= 5 and "=" not in segments[1]:
            result.update(
                {"level": segments[1], "service": segments[2], "trace_id": segments[3], "event": segments[4]}
            )
            if len(segments) > 5:
                result.update(_extract_tokens(" | ".join(segments[5:])))
            return result

    result.update(_extract_tokens(rest))
    if "trace_id" not in result and "requestId" in result:
        # The message-wrapped format names its correlation id "requestId"
        # instead of "trace_id" -- alias it so the same-trace identifier
        # correlation in parse_log_file() works uniformly across formats.
        result["trace_id"] = result["requestId"]

    message = result.pop("message", None)
    if message:
        # The message value is itself an escaped string of key="val" pairs
        # (e.g. from a "requestId=... function=... message=\"...\"" line).
        unescaped = message.replace('\\"', '"')
        nested = _extract_tokens(unescaped)
        for k, v in nested.items():
            result.setdefault(k, v)
        result.setdefault("service", result.get("function"))

    return result


def parse_handover_txt(path: Path, file_id: str) -> List[EvidenceRecord]:
    records: List[EvidenceRecord] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        parts = [p.strip() for p in line[2:].split("|")]
        if len(parts) < 5:
            continue
        ts_str, system, resource_id, category_hint, state = parts[:5]
        identifier_value = parts[5] if len(parts) > 5 else None
        captured_at = parse_timestamp(ts_str)
        category = classify_category(category_hint, resource_id)
        system_id = infer_system(category, system)
        row = {}
        if identifier_value and identifier_value.lower() != "no direct identifier":
            row["identifier"] = identifier_value
        identifiers = extract_identifiers(row)
        records.append(
            EvidenceRecord(
                file_id=file_id,
                system_id=system_id,
                category=category,
                resource_id=resource_id,
                state=state,
                identifiers=identifiers,
                captured_at=captured_at,
                raw={"line": line},
            )
        )
    return records


# Log events that only describe an attempt/diagnostic, not a completed
# read/check with a known result, carry no evidentiary weight on their own.
_SKIP_EVENT_SUBSTRINGS = (
    "_started",
    "retry",
    "recovered",
    "connection_acquired",
    "cache_refresh",
    "lease_extended",
    "consumer_lag",
)


def parse_log_file(path: Path, file_id: str) -> List[EvidenceRecord]:
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    parsed = [_parse_log_line(l) for l in lines]

    # A request's identifier is often only present on the "_started" line
    # of a trace (e.g. match_key/lookup_key), while the "_completed" line
    # that carries the result omits it. Correlate same-trace lines so the
    # identifier reaches the record built from the completed line.
    trace_identifiers: dict = {}
    for fields in parsed:
        trace_id = fields.get("trace_id")
        if not trace_id:
            continue
        ids = extract_identifiers(fields)
        if not ids.is_empty():
            trace_identifiers.setdefault(trace_id, type(ids)()).merge(ids)

    records: List[EvidenceRecord] = []
    for fields in parsed:
        event = fields.get("event", "")
        if not event or any(s in event for s in _SKIP_EVENT_SUBSTRINGS):
            continue
        resource_id = fields.get("resource_id")
        if not resource_id:
            continue

        service = fields.get("service")
        category = classify_category(event, service, resource_id)
        system_id = infer_system(category, service)
        identifiers = extract_identifiers(fields)
        trace_id = fields.get("trace_id")
        if trace_id and trace_id in trace_identifiers:
            identifiers.merge(trace_identifiers[trace_id])
        state = infer_state(fields)
        captured_at = parse_timestamp(fields.get("_timestamp"))

        records.append(
            EvidenceRecord(
                file_id=file_id,
                system_id=system_id,
                category=category,
                resource_id=resource_id,
                state=state,
                identifiers=identifiers,
                captured_at=captured_at,
                raw=fields,
            )
        )
    return records
