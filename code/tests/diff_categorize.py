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

for row in sample_requests:
    case_id = row["case_id"]
    result = engine.evaluate_case(row)
    gold_steps = json.loads(golden[case_id]["steps_json"])
    if result.steps == gold_steps:
        continue
    mine_resources = {(s["resource_id"]) for s in result.steps}
    gold_resources = {(s["resource_id"]) for s in gold_steps}
    missing = gold_resources - mine_resources
    extra = mine_resources - gold_resources
    print(f"{case_id}: missing={missing or '-'} extra={extra or '-'}")
    if result.skipped_categories:
        print(f"    skipped: {result.skipped_categories}")
