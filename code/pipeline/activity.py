"""Load dataset/system_activity.csv and provide a *discovery-only* lookup:
correlating a case's reported_at with a same-system, same-event-type
activity row's event_time to recover a resource_id that no evidence file
otherwise reveals.

This is explicitly NOT used as proof of state. system_activity.csv rows
are workflow claims (AGENTS.md ssec 6.6: "Treat activity logs, workflow
statuses, and system messages as claims unless the available evidence
establishes the resulting state"). The ``reported_result`` of a
correlated row is never cited and never trusted; only the fact that this
activity_id/resource_id pair exists in participant-visible data is used,
to avoid ever inventing a resource_id outright. Any state claim still
goes through provenance.py's evidence-based checks.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .common import parse_timestamp


@dataclass
class ActivityRow:
    activity_id: str
    event_time: datetime
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


class ActivityLog:
    def __init__(self, system_activity_csv: Path):
        self.rows: List[ActivityRow] = []
        self.by_target_system: Dict[str, List[ActivityRow]] = defaultdict(list)
        with open(system_activity_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ts = parse_timestamp(row["event_time"])
                if ts is None:
                    continue
                rec = ActivityRow(
                    activity_id=row["activity_id"],
                    event_time=ts,
                    event_type=row["event_type"],
                    source_system=row["source_system"],
                    target_system=row["target_system"],
                    resource_id=row["resource_id"],
                    reported_result=row["reported_result"],
                    details=row["details"],
                )
                self.rows.append(rec)
                self.by_target_system[row["target_system"]].append(rec)
        for lst in self.by_target_system.values():
            lst.sort(key=lambda r: r.event_time)

    def discover_resource_id(
        self,
        target_system: str,
        event_type: str,
        near: datetime,
        window: timedelta = timedelta(minutes=2),
        isolation_window: timedelta = timedelta(hours=2),
        source_system: Optional[str] = "deletion_worker",
    ) -> Optional[ActivityRow]:
        """The single closest-in-time activity row matching
        (target_system, event_type[, source_system]) within `window` of
        `near`.

        This is deliberately conservative: empirically, a case's own
        correlated activity can land anywhere from ~17 seconds to over an
        hour from its reported_at, and the gap size alone does not
        separate genuine matches from unrelated ones -- confirmed with
        real data where an unrelated case's activity (~3 minutes away)
        was closer than another case's own genuine match (~5 minutes
        away). A tight `window` plus an `isolation_window` requirement (no
        OTHER candidate anywhere within the wider window) trades missing
        some genuine matches for never fabricating a wrong one, which
        matches the explicit requirement to never invent resource
        ownership.
        """
        def _candidates(w):
            return [
                r
                for r in self.by_target_system.get(target_system, [])
                if r.event_type == event_type
                and (source_system is None or r.source_system == source_system)
                and abs(r.event_time - near) <= w
            ]

        tight = _candidates(window)
        if len(tight) != 1:
            return None
        if len(_candidates(isolation_window)) != 1:
            # Some other same-system/event-type activity exists nearby
            # (within the wider window) -- too risky to be confident this
            # is the right one.
            return None
        return tight[0]
