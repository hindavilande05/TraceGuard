"""Load user_identities.csv + user_audit_log.csv and resolve a request's
email to a user_id plus the full set of current and historical identifiers
for that user (so evidence written under an old email/phone can still be
found).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from .common import Identifiers, normalize_email, normalize_phone, parse_timestamp


@dataclass
class UserRecord:
    user_id: str
    current_name: str
    current_email: str
    current_phone: str
    current_email_hash: str
    updated_at: Optional[object]
    historical_emails: set = field(default_factory=set)
    historical_phones: set = field(default_factory=set)
    historical_email_hashes: set = field(default_factory=set)

    def all_identifiers(self) -> Identifiers:
        ids = Identifiers()
        if self.current_email:
            ids.emails.add(normalize_email(self.current_email))
        if self.current_phone:
            phone = normalize_phone(self.current_phone)
            if phone:
                ids.phones.add(phone)
        if self.current_email_hash:
            ids.email_hashes.add(self.current_email_hash.lower())
        if self.current_name:
            ids.names.add(self.current_name)
        ids.emails |= self.historical_emails
        ids.phones |= {normalize_phone(p) for p in self.historical_phones if normalize_phone(p)}
        ids.email_hashes |= {h.lower() for h in self.historical_email_hashes}
        ids.user_refs.add(self.user_id)
        return ids


class IdentityResolver:
    def __init__(self, identities_csv: Path, audit_log_csv: Path):
        self.users: Dict[str, UserRecord] = {}
        self._email_to_user_id: Dict[str, str] = {}

        with open(identities_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                user_id = row["user_id"]
                rec = UserRecord(
                    user_id=user_id,
                    current_name=row.get("name", ""),
                    current_email=row.get("email", ""),
                    current_phone=row.get("phone", ""),
                    current_email_hash=row.get("email_hash", ""),
                    updated_at=parse_timestamp(row.get("updated_at")),
                )
                self.users[user_id] = rec
                email = normalize_email(row.get("email"))
                if email:
                    self._email_to_user_id[email] = user_id

        with open(audit_log_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                user_id = row["user_id"]
                rec = self.users.get(user_id)
                if rec is None:
                    continue
                field_name = row["field_name"]
                old_value = row.get("old_value") or ""
                new_value = row.get("new_value") or ""
                if field_name == "email":
                    if old_value:
                        rec.historical_emails.add(normalize_email(old_value))
                        self._email_to_user_id.setdefault(normalize_email(old_value), user_id)
                    if new_value:
                        rec.historical_emails.add(normalize_email(new_value))
                        self._email_to_user_id.setdefault(normalize_email(new_value), user_id)
                elif field_name == "phone":
                    if old_value:
                        rec.historical_phones.add(old_value)
                    if new_value:
                        rec.historical_phones.add(new_value)
                elif field_name == "email_hash":
                    if old_value:
                        rec.historical_email_hashes.add(old_value)
                    if new_value:
                        rec.historical_email_hashes.add(new_value)

    def resolve_email(self, email: str) -> Optional[str]:
        """Resolve a request email (current or historical, case-insensitive)
        to a user_id."""
        norm = normalize_email(email)
        if not norm:
            return None
        return self._email_to_user_id.get(norm)

    def get(self, user_id: str) -> Optional[UserRecord]:
        return self.users.get(user_id)
