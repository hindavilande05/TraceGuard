"""Per-case state reconstruction: resolves identity, discovers resources,
applies deletion_rules.csv, and decides COMPLETE / INCOMPLETE /
NOT_APPLICABLE with evidence-backed steps.

Core policy (explicit, so it's auditable):
  - A category this pipeline cannot determine one way or the other (no
    direct evidence and no demonstrably-complete snapshot roster to check
    absence against) is SKIPPED, not guessed. It contributes to neither
    "violation found" nor "verified compliant". This means a COMPLETE
    result only ever contains steps for what was actually confirmed, and
    an INCOMPLETE result only ever contains findings that are directly
    evidenced -- never a fabricated one to fill a gap.
  - deletion_status is INCOMPLETE if, and only if, at least one concrete,
    evidence-backed violation was found. Otherwise COMPLETE.
  - All evidence lookups are bound to the case's own audit_at; nothing
    captured after it is ever consulted (see provenance.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import List, Optional

from .activity import ActivityLog
from .classifier import classify_request_text
from .common import parse_timestamp
from .evidence_loader import EvidenceStore
from .identities import IdentityResolver
from .provenance import CATEGORIES_IMPLIED_BY_USER_EXISTENCE, ProvenanceEngine
from .rules import Rule, load_rules
from .steps import Step, build_steps_json

# Categories handled by deletion_rules.csv compliance checks below.
# DATA_FLOW is a recreation-risk process, not a retained data category, and
# is handled separately (see _check_data_flows).
_POLICY_CATEGORIES_HANDLED_INLINE = {"DATA_FLOW"}


@dataclass
class CaseResult:
    case_id: str
    deletion_status: str
    steps: List[dict]
    # Diagnostics (not part of the output contract) for reporting/testing.
    skipped_categories: List[str] = field(default_factory=list)
    account_resource_id: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    classification_source: str = "model"
    classification_confidence: str = "high"
    classification_category: str = ""


class StateReconstructor:
    def __init__(
        self,
        resolver: IdentityResolver,
        store: EvidenceStore,
        activity_log: ActivityLog,
        rules_path,
        usage_tracker=None,
    ):
        self.resolver = resolver
        self.store = store
        self.provenance = ProvenanceEngine(store)
        self.activity_log = activity_log
        self.usage_tracker = usage_tracker
        self.rules = load_rules(rules_path)

    def _resolve_account_resource_id(self, grouped, reported_at, requested_at):
        account_keys = [k for k in grouped if k[1] == "USER_PROFILE"]
        if account_keys:
            key = next((k for k in account_keys if k[0] == "account_service"), account_keys[0])
            return key[2], "evidence"
        near = reported_at or requested_at
        if near is None:
            return None, None
        discovered = self.activity_log.discover_resource_id("account_service", "DELETE_RECORD", near=near)
        if discovered:
            return discovered.resource_id, "activity_timing_correlation"
        return None, None

    def evaluate_case(self, case_row: dict) -> CaseResult:
        case_id = case_row["case_id"]
        audit_at = parse_timestamp(case_row["audit_at"])
        requested_at = parse_timestamp(case_row["requested_at"])
        reported_at = parse_timestamp(case_row["reported_at"])

        classification = classify_request_text(case_row["request_text"])
        if self.usage_tracker is not None:
            self.usage_tracker.record(classification)
        if not classification.authorizes_deletion:
            return CaseResult(
                case_id,
                "NOT_APPLICABLE",
                [],
                classification_source=classification.source,
                classification_confidence=classification.confidence,
                classification_category=classification.category,
            )

        user_id = self.resolver.resolve_email(case_row["email"])
        if user_id is None:
            # Every request in this dataset resolves to a known user
            # (verified in Stage 1); this path exists only as a safe,
            # documented fallback and should not occur in practice.
            return CaseResult(
                case_id,
                "INCOMPLETE",
                [],
                notes=["identity could not be resolved from the supplied email; no steps can be evidence-backed"],
            )

        user_rec = self.resolver.get(user_id)
        identifiers = user_rec.all_identifiers()

        grouped = self.provenance.resources_by_category(identifiers, audit_at)

        findings: List[Step] = []
        remediation: List[Step] = []
        verification: List[Step] = []
        skipped: List[str] = []
        violation_found = False

        account_resource_id, discovery_method = self._resolve_account_resource_id(
            grouped, reported_at, requested_at
        )
        notes = []
        if discovery_method == "activity_timing_correlation":
            notes.append(
                f"account resource_id {account_resource_id} discovered via system_activity.csv "
                f"timing correlation (not evidence-cited; used for identification only)"
            )

        resources_to_check = dict(grouped)
        if account_resource_id is not None:
            key = ("account_service", "USER_PROFILE", account_resource_id)
            resources_to_check.setdefault(key, [])
        elif discovery_method is None:
            skipped.append("USER_PROFILE (account resource_id not discoverable from participant-visible data)")

        for (system_id, category, resource_id), _matches in resources_to_check.items():
            if category in _POLICY_CATEGORIES_HANDLED_INLINE:
                continue
            rule = self.rules.get(category)
            if rule is None:
                continue

            if category == "BACKUP_ARCHIVE":
                outcomes = self._check_backup(system_id, resource_id, audit_at)
                for outcome in outcomes:
                    kind, f_step, r_step, v_step = outcome
                    if f_step:
                        findings.append(f_step)
                    if r_step:
                        remediation.append(r_step)
                    if v_step:
                        verification.append(v_step)
                    if kind == "violation":
                        violation_found = True
                if not outcomes:
                    skipped.append(f"{category}:{resource_id}")
                continue

            outcome = self._check_category(
                system_id, category, resource_id, rule, audit_at, user_resolved=True
            )
            if outcome is None:
                skipped.append(f"{category}:{resource_id}")
                continue
            kind, f_step, r_step, v_step = outcome

            # Replica-drift risk only applies when the record's own
            # (primary) state was confirmed ABSENT/compliant -- if direct
            # evidence already shows a violation, that IS the finding;
            # checking drift too would double-count the same resource. If
            # drift is found on an otherwise-compliant resource, its
            # finding/remediation/verification replace (not add to) the
            # compliant outcome, since both would assert the same
            # VERIFY_RECORD_ABSENT.
            if kind == "compliant" and rule.required_action == "DELETE":
                drift = self.provenance.find_drift(resource_id, system_id, category, audit_at)
                if drift:
                    f_step = Step("FINDING", system_id, resource_id, "REPORT_RESTORE_RISK", "ACTIVE", drift[0].file_id)
                    r_step = Step("REMEDIATION", system_id, resource_id, "DELETE_RECORD", "ABSENT", "")
                    v_step = Step("VERIFICATION", system_id, resource_id, "VERIFY_RECORD_ABSENT", "ABSENT", "")
                    kind = "violation"

            if f_step:
                findings.append(f_step)
            if r_step:
                remediation.append(r_step)
            if v_step:
                verification.append(v_step)
            if kind == "violation":
                violation_found = True

        for (system_id, category, resource_id), _matches in grouped.items():
            if category != "DATA_FLOW":
                continue
            outcome = self._check_data_flow(system_id, resource_id, audit_at)
            if outcome is None:
                skipped.append(f"DATA_FLOW:{resource_id}")
                continue
            kind, f_step, r_step, v_step = outcome
            if f_step:
                findings.append(f_step)
            if r_step:
                remediation.append(r_step)
            if v_step:
                verification.append(v_step)
            if kind == "violation":
                violation_found = True

        status = "INCOMPLETE" if violation_found else "COMPLETE"
        if not violation_found and not verification:
            # Zero evidence exists anywhere (at or before audit_at) for
            # this subject, in any category. This is deliberately still
            # COMPLETE, not a special-cased INCOMPLETE: sample_output.csv
            # itself confirms this convention on real labeled data
            # (case_02 and case_04 are both exactly this situation --
            # zero discoverable evidence in either direction -- and both
            # are labeled COMPLETE in the golden answer). An initial
            # attempt to instead report INCOMPLETE-with-no-steps here,
            # reasoning from the English "sufficient evidence" wording in
            # isolation, was reverted once checked against this labeled
            # data and found to contradict it on 2/32 sample cases.
            notes.append(
                "zero participant-visible evidence (at or before audit_at) exists anywhere for this "
                "subject; COMPLETE with no steps, consistent with sample_output.csv's own convention "
                "for this exact situation (see case_02/case_04)"
            )
        if status == "INCOMPLETE":
            # Every verification step in an INCOMPLETE case describes a
            # check that must pass AFTER remediation -- even one whose
            # category was already compliant before remediation touched
            # anything else -- so it carries no evidence_id (confirmed
            # against sample_output.csv: e.g. case_11's VERIFY_RECORD_PRESENT
            # for an already-compliant retained audit record still has
            # evidence_id=""). Findings keep their real citations; only
            # verification is blanked.
            verification = [
                Step(v.phase, v.system_id, v.resource_id, v.operation, v.state, "") for v in verification
            ]
            steps = findings + remediation + verification
        else:
            steps = verification
        return CaseResult(
            case_id,
            status,
            build_steps_json(steps),
            skipped_categories=skipped,
            account_resource_id=account_resource_id,
            notes=notes,
            classification_source=classification.source,
            classification_confidence=classification.confidence,
            classification_category=classification.category,
        )

    def _check_category(self, system_id, category, resource_id, rule: Rule, audit_at, user_resolved: bool):
        """Returns (kind, finding_step_or_None, remediation_step_or_None,
        verification_step_or_None) where kind is "violation" or
        "compliant", or None if this category cannot be determined."""
        direct = self.provenance.latest_direct_evidence(resource_id, audit_at)

        if rule.required_action == "RETAIN":
            if direct is not None:
                return (
                    "compliant",
                    None,
                    None,
                    Step("VERIFICATION", system_id, resource_id, "VERIFY_RECORD_PRESENT", "PRESENT", direct.file_id),
                )
            return None

        # An explicit negative state (e.g. the handover report's
        # "NOT_FOUND" column, or a JSON ledger's result=NOT_FOUND) is
        # authoritative and must be checked BEFORE falling back to
        # "identifiers present -> violation": several evidence formats
        # retain the *searched* identifier as the lookup key even when the
        # documented result is that nothing was found, so the mere
        # presence of an identifier value on that record is not evidence
        # of a remaining record.
        #
        # ACCOUNT_TOMBSTONE is excluded from the "DELETED" branch
        # specifically: a tombstone's own account_status field is always
        # "DELETED" (describing the deleted *parent account*, which is the
        # entire reason the tombstone exists) -- it says nothing about
        # whether the tombstone record itself is still exposing PII.
        # Confirmed against real evidence: tombstone_9178 has
        # account_status=DELETED while still carrying name/email/phone/
        # hash, i.e. it is PRESENT and NOT redacted.
        negative_states = ("NOT_FOUND", "ABSENT") if category == "ACCOUNT_TOMBSTONE" else ("NOT_FOUND", "ABSENT", "DELETED")
        if direct is not None and (direct.state or "").upper() in negative_states:
            return (
                "compliant",
                None,
                None,
                Step("VERIFICATION", system_id, resource_id, "VERIFY_RECORD_ABSENT", "ABSENT", direct.file_id),
            )

        if direct is not None and not direct.identifiers.is_empty():
            op_map = {
                "DELETE": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT", "ABSENT"),
                "REDACT": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED", "REDACTED"),
                "ANONYMIZE": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
            }
            finding_op, remediation_op, verify_op, target_state = op_map[rule.required_action]
            finding_state = "ACTIVE" if finding_op.startswith("REPORT_ACTIVE") else "PRESENT"
            return (
                "violation",
                Step("FINDING", system_id, resource_id, finding_op, finding_state, direct.file_id),
                Step("REMEDIATION", system_id, resource_id, remediation_op, target_state, ""),
                Step("VERIFICATION", system_id, resource_id, verify_op, target_state, ""),
            )

        if direct is not None and direct.identifiers.is_empty():
            if rule.required_action == "REDACT":
                return (
                    "compliant",
                    None,
                    None,
                    Step("VERIFICATION", system_id, resource_id, "VERIFY_RECORD_REDACTED", "REDACTED", direct.file_id),
                )
            if rule.required_action == "ANONYMIZE":
                return (
                    "compliant",
                    None,
                    None,
                    Step(
                        "VERIFICATION", system_id, resource_id, "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", direct.file_id
                    ),
                )
            return None

        absence = self.provenance.check_absence_by_snapshot(
            resource_id,
            system_id,
            category,
            audit_at,
            user_resolved=user_resolved and category in CATEGORIES_IMPLIED_BY_USER_EXISTENCE,
        )
        if absence.status == "ABSENT_CONFIRMED":
            return (
                "compliant",
                None,
                None,
                Step("VERIFICATION", system_id, resource_id, "VERIFY_RECORD_ABSENT", "ABSENT", absence.file_id),
            )
        return None

    def _check_backup(self, system_id, resource_id, audit_at):
        """BACKUP_ARCHIVE is RETAIN-but-block-restore: the archive itself
        should stay present (disaster-recovery policy), but if restore to
        production is still possible, that's a recreation-risk finding
        distinct from the presence check. Returns a list of 0-2 outcomes
        (restore-risk outcome, presence outcome)."""
        direct = self.provenance.latest_direct_evidence(resource_id, audit_at)
        if direct is None:
            return []
        outcomes = []
        if direct.state == "ACTIVE":
            outcomes.append(
                (
                    "violation",
                    Step("FINDING", system_id, resource_id, "REPORT_RESTORE_RISK", "ACTIVE", direct.file_id),
                    Step("REMEDIATION", system_id, resource_id, "BLOCK_RESTORE", "BLOCKED", ""),
                    Step("VERIFICATION", system_id, resource_id, "VERIFY_RESTORE_BLOCKED", "BLOCKED", ""),
                )
            )
        elif direct.state == "BLOCKED":
            outcomes.append(
                (
                    "compliant",
                    None,
                    None,
                    Step("VERIFICATION", system_id, resource_id, "VERIFY_RESTORE_BLOCKED", "BLOCKED", direct.file_id),
                )
            )
        outcomes.append(
            (
                "compliant",
                None,
                None,
                Step("VERIFICATION", system_id, resource_id, "VERIFY_RECORD_PRESENT", "PRESENT", direct.file_id),
            )
        )
        return outcomes

    def _check_data_flow(self, system_id, resource_id, audit_at):
        direct = self.provenance.latest_direct_evidence(resource_id, audit_at)
        if direct is None:
            return None
        if direct.state == "ACTIVE":
            return (
                "violation",
                Step("FINDING", system_id, resource_id, "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", direct.file_id),
                Step("REMEDIATION", system_id, resource_id, "DISABLE_DATA_FLOW", "DISABLED", ""),
                Step("VERIFICATION", system_id, resource_id, "VERIFY_DATA_FLOW_DISABLED", "DISABLED", ""),
            )
        if direct.state == "DISABLED":
            return (
                "compliant",
                None,
                None,
                Step("VERIFICATION", system_id, resource_id, "VERIFY_DATA_FLOW_DISABLED", "DISABLED", direct.file_id),
            )
        return None
