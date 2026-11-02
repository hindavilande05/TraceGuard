"""Load dataset/deletion_rules.csv and expose the required treatment per
data category.

Most rules are unconditional ("Always"). The conditional rules' condition
text is kept for transparency, but is treated as equivalent to the
category's steady-state compliance check already performed by the state-
reconstruction engine, per this explicit, documented reasoning:

  - SUPPORT_MESSAGE (REDACT "if conversation contains shared ... context
    that must be retained") / SUPPORT_ATTACHMENT (DELETE "if attachment
    belongs only to the deletion subject"): every support record and
    attachment this pipeline discovers is tied to the deletion subject by
    construction (that's how it was found -- via their own identifiers),
    and support conversations in this dataset are, by their nature,
    shared with a support agent. No participant-visible signal
    distinguishes a "subject-only" message from a "shared" one at the
    message level, so the category-level default (REDACT messages, DELETE
    attachments) is applied uniformly. This is a documented judgment call,
    not a discovered exception.
  - ANALYTICS_PROFILE / PRODUCT_TELEMETRY ("if still attributable to a
    user"): the condition IS the violation check -- "still attributable"
    means identifiers are still present, which is exactly what
    state.py compares the current evidence against. The required action
    (ANONYMIZE) is the target state; a record that already has no
    identifiers is already compliant, independent of how that's phrased.
  - EMAIL_DELIVERY_LOG ("if log contains a user email address"): same
    reasoning -- the condition is the compliance check itself.
  - SECURITY_AUDIT_LOG / LEGAL_HOLD_RECORD / BACKUP_ARCHIVE (RETAIN "if"
    retention/hold/restorability applies): every instance of these
    categories actually discovered in this dataset's evidence carries
    retention/hold metadata supporting the condition (far-future
    retention_until / retained_until dates, active legal holds). Absent
    contrary evidence, retention-protecting categories are treated as
    satisfying their condition -- the safer default for a policy whose
    purpose is to prevent deletion of records that may be legally or
    operationally required, which matches "do not assume every remaining
    record is a violation."
  - ACCOUNT_TOMBSTONE ("if account is deleted and only a stable user_id is
    needed"): by definition, a tombstone record only exists once an
    account has been deleted, so discovering one already satisfies the
    condition.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, NamedTuple


class Rule(NamedTuple):
    rule_id: str
    condition: str
    required_action: str  # "DELETE" | "REDACT" | "ANONYMIZE" | "RETAIN"


def load_rules(deletion_rules_csv: Path) -> Dict[str, Rule]:
    rules: Dict[str, Rule] = {}
    with open(deletion_rules_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rules[row["data_category"]] = Rule(
                rule_id=row["rule_id"],
                condition=row["condition"],
                required_action=row["required_action"],
            )
    return rules
