#!/usr/bin/env python3
"""Ghost Data Still Exists -- deletion audit agent entry point.

Reads dataset/deletion_requests.csv, reconstructs each case's state from
participant-visible evidence only, and writes output.csv at the repo root
with exactly one row per case, sorted by case_id.

Run from the repo root with the project virtualenv:
    ./.venv/bin/python3 code/main.py

Requires a local Ollama server running qwen2.5:7b (see README.md) for the
request-intent classification step; everything else is deterministic
Python. Prints a short run summary (status distribution, usage) to
stdout -- see code/evaluation/ for the full evaluation and usage reports.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import config
from pipeline.activity import ActivityLog
from pipeline.evidence_loader import EvidenceStore
from pipeline.identities import IdentityResolver
from pipeline.state import StateReconstructor
from pipeline.usage import UsageTracker


def case_sort_key(case_id: str) -> int:
    return int(case_id.rsplit("_", 1)[1])


def run(write_output: bool = True):
    t0 = time.time()
    print("Loading identities, evidence, activity, and rules...")
    resolver = IdentityResolver(config.USER_IDENTITIES_CSV, config.USER_AUDIT_LOG_CSV)
    store = EvidenceStore()
    store.load_all(config.EVIDENCE_FILES_CSV, config.EVIDENCE_DIR)
    activity_log = ActivityLog(config.SYSTEM_ACTIVITY_CSV)
    usage = UsageTracker(model=config.OLLAMA_MODEL)
    engine = StateReconstructor(resolver, store, activity_log, config.DELETION_RULES_CSV, usage_tracker=usage)
    print(f"Setup complete in {time.time() - t0:.1f}s")

    with open(config.DELETION_REQUESTS_CSV, newline="", encoding="utf-8") as f:
        requests = list(csv.DictReader(f))
    requests.sort(key=lambda r: case_sort_key(r["case_id"]))
    print(f"Loaded {len(requests)} cases from {config.DELETION_REQUESTS_CSV.name}")

    results = []
    t1 = time.time()
    for i, row in enumerate(requests, 1):
        result = engine.evaluate_case(row)
        results.append(result)
        if i % 50 == 0 or i == len(requests):
            print(f"  processed {i}/{len(requests)} cases ({time.time() - t1:.1f}s elapsed)")
    usage.finish()

    if write_output:
        with open(config.OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["case_id", "deletion_status", "steps_json"])
            for r in results:
                writer.writerow([r.case_id, r.deletion_status, json.dumps(r.steps, separators=(",", ":"))])
        print(f"Wrote {len(results)} rows to {config.OUTPUT_CSV}")

    status_counts = {}
    for r in results:
        status_counts[r.deletion_status] = status_counts.get(r.deletion_status, 0) + 1
    print("\nStatus distribution:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    print(
        f"\nClassifier usage: {usage.live_calls} live calls, {usage.cache_hits} cache hits, "
        f"{usage.fallback_calls} fallback, {usage.total_tokens} total tokens, "
        f"{usage.wall_clock_seconds:.1f}s wall clock"
    )

    return results, usage


if __name__ == "__main__":
    run()
