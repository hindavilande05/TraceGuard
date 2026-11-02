"""Deterministic step assembly: enforces phase order, the operation order
table from problem_statement.md, system_id/resource_id tiebreaks, and
consecutive step numbering. This is the only place that decides step
order and numbering -- state.py only decides *which* steps exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

PHASE_ORDER = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}

_OPERATION_ORDER = {
    # FINDING
    "REPORT_REMAINING_RECORD": 0,
    "REPORT_EXPOSED_PII": 1,
    "REPORT_ACTIVE_DATA_FLOW": 2,
    "REPORT_RESTORE_RISK": 3,
    # REMEDIATION
    "DISABLE_DATA_FLOW": 0,
    "BLOCK_RESTORE": 1,
    "DELETE_RECORD": 2,
    "REDACT_RECORD": 3,
    "ANONYMIZE_RECORD": 4,
    # VERIFICATION
    "VERIFY_RECORD_ABSENT": 0,
    "VERIFY_RECORD_PRESENT": 1,
    "VERIFY_RECORD_REDACTED": 2,
    "VERIFY_RECORD_ANONYMIZED": 3,
    "VERIFY_DATA_FLOW_DISABLED": 4,
    "VERIFY_RESTORE_BLOCKED": 5,
}


@dataclass
class Step:
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str  # "" for proposed remediation / future verification


def build_steps_json(steps: List[Step]) -> List[dict]:
    ordered = sorted(
        steps,
        key=lambda s: (
            PHASE_ORDER[s.phase],
            _OPERATION_ORDER[s.operation],
            s.system_id or "",
            s.resource_id or "",
        ),
    )
    return [
        {
            "step_no": i + 1,
            "phase": s.phase,
            "system_id": s.system_id,
            "resource_id": s.resource_id,
            "operation": s.operation,
            "state": s.state,
            "evidence_id": s.evidence_id,
        }
        for i, s in enumerate(ordered)
    ]
