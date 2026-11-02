import csv, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config
from pipeline.activity import ActivityLog
from pipeline.evidence_loader import EvidenceStore
from pipeline.identities import IdentityResolver
from pipeline.state import StateReconstructor

resolver = IdentityResolver(config.USER_IDENTITIES_CSV, config.USER_AUDIT_LOG_CSV)
store = EvidenceStore(); store.load_all(config.EVIDENCE_FILES_CSV, config.EVIDENCE_DIR)
activity_log = ActivityLog(config.SYSTEM_ACTIVITY_CSV)
engine = StateReconstructor(resolver, store, activity_log, config.DELETION_RULES_CSV)

sample_requests = list(csv.DictReader(open(config.SAMPLE_REQUESTS_CSV, newline="", encoding="utf-8")))
golden = {r["case_id"]: r for r in csv.DictReader(open(config.SAMPLE_OUTPUT_CSV, newline="", encoding="utf-8"))}

status_match = 0
exact_step_match = 0
total_gold_steps = total_mine_steps = 0
matched_pairs = 0
field_matches = {"phase": 0, "operation": 0, "state": 0, "evidence_presence": 0}
field_total = 0

for row in sample_requests:
    case_id = row["case_id"]
    result = engine.evaluate_case(row)
    gold = golden[case_id]
    gold_steps = json.loads(gold["steps_json"])

    if result.deletion_status == gold["deletion_status"]:
        status_match += 1
    if result.steps == gold_steps:
        exact_step_match += 1

    mine_by_key = {(s["resource_id"], s["operation"]): s for s in result.steps}
    gold_by_key = {(s["resource_id"], s["operation"]): s for s in gold_steps}
    total_gold_steps += len(gold_steps)
    total_mine_steps += len(result.steps)

    for key, gstep in gold_by_key.items():
        if key in mine_by_key:
            matched_pairs += 1
            mstep = mine_by_key[key]
            field_total += 1
            if mstep["phase"] == gstep["phase"]:
                field_matches["phase"] += 1
            if mstep["operation"] == gstep["operation"]:
                field_matches["operation"] += 1
            if mstep["state"] == gstep["state"]:
                field_matches["state"] += 1
            if (mstep["evidence_id"] == "") == (gstep["evidence_id"] == ""):
                field_matches["evidence_presence"] += 1

precision = matched_pairs / total_mine_steps if total_mine_steps else 0
recall = matched_pairs / total_gold_steps if total_gold_steps else 0

print(f"case-level status accuracy: {status_match}/32 = {status_match/32:.1%}")
print(f"exact full-sequence match: {exact_step_match}/32 = {exact_step_match/32:.1%}")
print(f"total gold steps: {total_gold_steps}, total predicted steps: {total_mine_steps}")
print(f"matched (resource_id, operation) pairs: {matched_pairs}")
print(f"step-pair precision: {precision:.1%}  (of predicted steps, how many match a real gold step)")
print(f"step-pair recall: {recall:.1%}  (of gold steps, how many we also predicted)")
print(f"field accuracy among matched pairs (n={field_total}):")
for field, count in field_matches.items():
    print(f"  {field}: {count}/{field_total} = {count/field_total:.1%}" if field_total else f"  {field}: n/a")
