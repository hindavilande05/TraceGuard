# Evaluation Report — Ghost Data Still Exists

This report covers the full 518-case run that produced the submitted `output.csv`, plus everything measurable against the only labeled reference available: `dataset/sample_output.csv` (32 cases). **There is no hidden/golden answer for the other 486 cases**, so every number for the full 518-case set is a structural, internal-consistency, or manual-audit result — never a claimed accuracy figure. Sample-set agreement is reported only for the sample, and is explicitly not presented as full-dataset ground truth.

## 1. Sample-set accuracy (32 labeled cases)

| Metric | Result |
|---|---|
| Case-level `deletion_status` accuracy | **32/32 = 100.0%** |
| Exact full-step-sequence match | 5/32 = 15.6% |
| Step-pair precision (of steps we predicted, how many match a real gold step by `(resource_id, operation)`) | 146/166 = **88.0%** |
| Step-pair recall (of gold steps, how many we also predicted) | 146/193 = **75.6%** |
| Field accuracy on matched step-pairs (`phase`, `operation`, `state`, evidence-presence pattern) | **100.0%** on all four, for every one of the 146 matched pairs |

Interpretation: every time our step set and the golden step set agree on *which* resource and *what action*, they agree completely on every other field. The gap between 100% status accuracy and 15.6% exact-sequence match is entirely about *which resources get a step at all* — covered in detail in Section 2.

## 2. The 27 sample cases with step-level differences

27 of the 32 sample cases produce a steps array that differs from `sample_output.csv` (listed individually below). **Every one of the 27 was individually investigated against the raw dataset files** (not assumed, not pattern-matched) and falls into two categories, which are not mutually exclusive — a single case can have both an omission and an extra finding at once. One case (`case_14`) has neither and is explained separately at the end of this section. Per explicit instruction, none were "fixed" to force a match — both categories are the result of principled rules applied consistently. The exact breakdown, counted directly from `code/tests/diff_categorize.py` output rather than estimated:

| | Count | Cases |
|---|---|---|
| Has a Category A omission (with or without a Category B addition) | 24 | all 27 except `case_12`, `case_14`, `case_17` |
| Has a Category B addition (with or without a Category A omission) | 6 | `case_08, 12, 17, 21, 24, 29` |
| Has **both** A and B in the same case | 4 | `case_08, 21, 24, 29` |
| Has **only** a Category B addition, no omission | 2 | `case_12, case_17` |
| Has **only** a Category A omission, no addition | 20 | the remaining 20 of the 24 above |
| Neither (step-count/ordering noise only) | 1 | `case_14` |

(24 + 6 − 4 overlap + 1 neither = 27, the full set of differing cases.)

### Category A — Legitimate omission: resource genuinely undiscoverable from participant-visible data (24 of 27 cases)

For some resources (most commonly the deletion subject's own `account_NNN`, but also some `analytics_NNN` / `search_NNN` / `audit_record_NNN`), **zero participant-visible evidence of any kind** — no evidence file, no `system_activity.csv` row close enough to attribute safely — reveals that specific resource ID for that specific case, confirmed by direct `grep` across the entire `dataset/` tree. The golden answer still cites a resource ID in these cases (e.g. `account_004` for `case_04`), which can only come from the organizer's private generator state. Per `AGENTS.md` ("Organizer-only truth... must never be used for predictions" and "Do not invent evidence IDs, resource ownership, actions, or proof"), the pipeline **omits** the step rather than fabricate a resource ID or a false absence claim. This was confirmed to be the right call, not a shortfall, by cross-checking the omission logic from both directions:

- `check_absence_by_snapshot()` requires a demonstrably-complete snapshot roster **and** independent prior evidence the resource existed, before treating an omission as proof of deletion — see `code/pipeline/provenance.py`.
- A deliberately conservative resource-discovery fallback (`code/pipeline/activity.py`) was built, tuned, and empirically validated specifically to try to recover these: it correctly recovers **15 of 32** sample cases' account resource IDs via tight timing correlation with `system_activity.csv` (with an explicit isolation check so it never guesses when another candidate is anywhere nearby), and was proven to produce **zero false positives** across all 32 cases by direct comparison against gold. It still correctly abstains on the other cases because no safe, unambiguous correlation exists for them (verified: in one case an *unrelated* case's activity was objectively closer in time than another case's own genuine match, proving that timing proximity alone cannot be trusted at a wider window).

All 24 cases: `case_02, 03, 04, 06, 08, 09, 10, 15, 16, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32` — each with one or more omitted sub-findings (most commonly the subject's own `account_NNN`; `case_03` and several others also omit an `analytics_NNN`/`search_NNN`/`audit_record_NNN`).

### Category B — Extra, genuinely true, evidence-backed finding not in gold's curated scope (6 of 27 cases)

In `case_08, 12, 17, 21, 24, 29` (6 cases, not 4 — see the table above), the pipeline reports an additional `legal_service` (`LEGAL_HOLD_RECORD`) retention verification, and in `case_17` also a second `account_service` resource and an `identity_service` tombstone redaction check. Of these 6, four (`case_08, 21, 24, 29`) also have a Category A omission elsewhere in the same case, and two (`case_12, case_17`) have no omission at all — gold matches every resource the pipeline found missing-free, but the pipeline still reports one additional true resource gold didn't include. Every one of the 6 was manually verified to be **true and directly evidenced**:

- `case_08`'s subject genuinely resolves to `user_08`, and `evidence/documents/legal_retention_register_20260529.pdf` genuinely pairs `user_08` with order `ORD-20264118` under an active legal hold — confirmed by reading the PDF directly, not inferred from ID-number coincidence.
- `case_17`'s subject (Léonie Girard) is genuinely `user_17`, and a 2026-03-10 point-in-time readback log (`evidence_67`) genuinely shows `account_6523` looked up with her exact email and returned `NOT_FOUND` — a second, independently-evidenced account resource for the same person.

These are not errors: they are correct, grounded facts about the same resolved subject that golden's curated per-case answer does not happen to include (most likely because the generator's "relevant resource set" per case is a deliberately bounded subset, and these particular linked records happen to sit just outside it, similar in spirit to the decoys `AGENTS.md` warns are present in the global data — except here the extra fact is real, just out-of-scope for that case's canonical answer rather than a decoy). No attempt was made to suppress genuinely true, evidence-backed output to improve the match score.

### Full per-case diff summary

```
case_02: missing={'account_002'}
case_03: missing={'analytics_9406'}
case_04: missing={'account_004', 'analytics_901'}
case_06: missing={'account_006'}
case_08: missing={'account_008'}  extra={'ORD-20264118'}
case_09: missing={'audit_record_4902', 'account_3960'}
case_10: missing={'account_010'}
case_12: extra={'ORD-20264207'}
case_14: (field-level only; see note below)
case_15: missing={'audit_record_1596'}
case_16: missing={'search_1007'}
case_17: extra={'ORD-20264319', 'account_6523', 'user_17'}
case_18: missing={'account_5521', 'audit_record_6645'}
case_19: missing={'audit_record_6793'}
case_20: missing={'account_5037', 'audit_record_5569'}
case_21: missing={'account_6440', 'analytics_5335'}  extra={'ORD-20264402'}
case_22: missing={'account_4990', 'search_8911'}
case_23: missing={'audit_record_4128', 'account_410'}
case_24: missing={'audit_record_7095'}  extra={'ORD-20264511'}
case_25: missing={'analytics_1895', 'account_3136'}
case_26: missing={'account_5684', 'analytics_4566'}
case_27: missing={'analytics_4574', 'account_3866'}
case_28: missing={'analytics_6077', 'account_5482'}
case_29: missing={'account_7012', 'audit_record_3954'}  extra={'ORD-20264600'}
case_30: missing={'audit_record_1707', 'account_4163'}
case_31: missing={'account_4857'}
case_32: missing={'account_8858'}
```

(`case_14`'s resource set matches gold exactly; the remaining difference is step ordering/count noise from a single-finding case and was confirmed to not represent a logic error.)

## 3. Full 518-case run

### Status distribution

| Status | Count | % |
|---|---|---|
| COMPLETE | 170 | 32.8% |
| INCOMPLETE | 338 | 65.3% |
| NOT_APPLICABLE | 10 | 1.9% |

### Structural validation (`code/tests/validate_output.py`)

**0 errors.** Every row has a valid status, valid JSON, consecutive step numbering starting at 1, correct FINDING→REMEDIATION→VERIFICATION phase ordering, correct operation/system_id/resource_id ordering within phase, correct `evidence_id` blanking rules (empty for all REMEDIATION steps and for all VERIFICATION steps of an INCOMPLETE case; non-empty for every FINDING and for VERIFICATION steps of a COMPLETE case), and `[]` for every `NOT_APPLICABLE` row.

**2 warnings**, both `COMPLETE` cases with zero verification steps (`case_61`, `case_63`) — see Section 3a for the explicit investigation of this exact situation, requested directly by the user before packaging.

### Full-dataset invariants (`code/tests/test_stage6_full_dataset.py`)

All pass: exactly 518 rows, every `case_id` matches `deletion_requests.csv`, no `NOT_APPLICABLE` row has non-empty steps, **every cited `evidence_id` exists in `evidence_files.csv` (zero fabricated citations)**, and **zero cases cite evidence captured after that case's own `audit_at`** (see the temporal-integrity bug fixed in Section 5).

### Per-case audit summary (`code/tests/audit_summary.py`)

| Metric | Count |
|---|---|
| Low-confidence classifications flagged for review | 0 |
| Fallback-heuristic classifications (model unavailable) | 0 |
| Cases where the account resource could not be safely discovered | 351 (67.8%) |
| Other inconclusive category checks skipped | 10 (all `SEARCH_INDEX_ENTRY`) |
| Active-data-flow findings (recreation risk, evidenced as of `audit_at`) | 111 |
| Active-restore-risk findings | 24 |
| Tombstone PII-exposure findings | 4 (`case_544, 546, 548, 550` — all correctly `INCOMPLETE`) |
| `COMPLETE` cases with zero verification steps | 2 (`case_61`, `case_63` — see Section 3a) |
| Cases with unresolved identity | 0 |

The 67.8% account-discovery abstention rate reflects the dataset's actual evidence density (94 evidence files for 518 cases) combined with a deliberately conservative discovery policy, not a defect — see Section 4 for the direct verification that this conservatism causes zero false attributions.

### 3a. Investigation: `COMPLETE` cases with zero steps — does the spec permit this?

This was investigated explicitly and in depth, as requested, before packaging. **Before this investigation, 26 of 518 cases were `COMPLETE` with zero steps.** Treating that number as suspicious (rather than assuming it was fine) led to finding and fixing **5 previously-undiscovered evidence-parsing bugs**, each a distinct `.log` file format never seen before in this dataset:

1. A bracketed format (`[LEVEL] [service] [trace] event_name: key="val"`, `application_trace_*.log`) where the event name is a bare word before a colon, not `event=...` — the parser required `event=` and silently extracted zero records from this entire file format.
2. A `message="<escaped key=\"val\" pairs>"` wrapper (`cloud_runtime_export_*.log`) where the real fields are nested inside an escaped string value.
3. A pipe-delimited format (`TS | LEVEL | service | trace | event | key=val`, `operations_stream_*.log`).
4. The message-wrapped format's correlation id field is named `requestId`, not `trace_id` — breaking the existing same-trace identifier correlation (an identifier seen on a "started" line reaching the paired "completed" line) even once format 2 was parsed.
5. An RFC5424-style syslog header (`<15>1 node-X service trace-id - -`) where the trace id is a *bare positional token*, not `key=value` — also broke trace correlation for that file family.

Fixing these (verified via direct `grep` cross-checks against the raw files before and after each fix, and full regression + sample re-runs showing no loss of the 32/32 sample status match) **reduced the zero-step `COMPLETE` count from 26 to 2**, and in the process **surfaced genuine remaining-record violations that were previously hidden** — e.g. `account_1334`/`account_4921`/`account_7107` were all found `FOUND`/`http_status=200` (still present) once these formats parsed correctly, correctly flipping several cases from `COMPLETE` to `INCOMPLETE`. Full-run status counts shifted accordingly: COMPLETE 187→170, INCOMPLETE 321→338 (NOT_APPLICABLE unchanged at 10, since the classifier wasn't touched).

**For the final 2 cases (`case_61`, `case_63`), `COMPLETE` with zero steps was confirmed correct, not a gap**, through:
- An initial attempt to change the policy (any case with zero steps becomes `INCOMPLETE`, on the reasoning that "COMPLETE requires sufficient evidence") was implemented, then **checked against `sample_output.csv` and found to contradict it**: `case_02` and `case_04` are the exact same situation (zero discoverable evidence in either direction for the account) and are both labeled `COMPLETE` in the golden answer. The policy change was reverted once this was discovered, rather than kept despite contradicting labeled ground truth.
- Both `case_61` and `case_63` were then individually, exhaustively verified: every current and historical identifier (email, phone, email hash) for each subject was searched across **every** evidence file (including OCR'd image/PDF text), at any time, not just before `audit_at`. `case_61`'s subject does appear in one PDF (`evidence_58`), but it was captured 2026-09-12 — after `case_61`'s own `audit_at` of 2026-02-14 — and is correctly excluded as future evidence. `case_63`'s subject appears in **zero** evidence files, ever. Both are confirmed, exhaustively, to have no eligible evidence, and `COMPLETE` with no steps is the dataset's own established convention for this exact situation.

## 4. Risk-based review (instruction 6)

- **All 10 `NOT_APPLICABLE` cases were read individually.** Every one is an unambiguous non-deletion request (profile correction, data export/access, marketing opt-out, login help, email change) and every one explicitly states it is *not* asking for deletion. **0 false positives.**
- **Insufficient snapshot scope / false absence:** `check_absence_by_snapshot()` requires both a demonstrably-complete roster and independent prior evidence before confirming absence (tested directly in `test_stage2_provenance.py` and `test_stage5_state.py`); checked that no `COMPLETE` case in the full run contains a step with state `ACTIVE` leaking through — **0 found**, confirming no in-progress recreation risk is ever silently marked complete.
- **Historical identifiers:** 22 `COMPLETE` cases in the full run were resolved via a *historical* (not current) email. Spot-checked 3 directly against raw evidence (e.g. `case_41`'s `evidence_03` citation for `account_4051` was confirmed to literally appear in the handover report under the historical email `lucas-souza95@gmail.com`).
- **Tombstone PII:** both directions were verified on real data — 4 cases where a tombstone still exposes PII are correctly `INCOMPLETE`, and 5 `COMPLETE` cases citing a compliant, fully-redacted tombstone were confirmed correct.
- **Active sync/restore risk:** confirmed 0 `COMPLETE` cases contain any `ACTIVE` state anywhere in their steps (the only way a `COMPLETE` case could hide a live risk).
- **Evidence after `audit_at`:** a real violation was found and fixed during this stage (see below) — now 0 violations across all 518 cases, enforced as a hard invariant in `test_stage6_full_dataset.py`.

## 5. Failures found and improvements made

### During Stage 5 (state reconstruction), found via iterating strictly on real sample mismatches:
1. Evidence formats retaining a `NOT_FOUND` record's searched identifier as its lookup key were misread as a remaining record.
2. A replica-drift check double-counted a resource already flagged by direct evidence.
3. 2 of 26 `.log` files use JSON-per-line format, entirely unparsed by the key=value log parser.
4. A hash value was only recognized when the key name contained "hash"; missed a real case under `correlation_key`.
5. JSON search-index records nest fields inside an unflattened `"document"` object.
6. An OCR'd multi-subject screenshot pooled every identifier on the page onto every resource, cross-contaminating different customers — fixed with row-windowed attribution.
7. Tesseract non-deterministically renders the same image's underscore as a space across runs, silently breaking resource-id matching.
8. A tombstone's `account_status: DELETED` field (always true for a tombstone, by definition) was wrongly read as "this record is absent" instead of checking actual PII exposure.
9. A resource-discovery timing-correlation fallback produced real false positives when widened for recall; rebuilt with a strict isolation requirement, verified to produce zero false positives.

### During this stage (full-run validation and pre-packaging audit):
10. **Temporal-integrity bug**: a few `.log` files' individual lines carry timestamps earlier than the file's own `evidence_files.csv` manifest `captured_at` (the file's export/collection time). The pipeline was citing the line's own earlier timestamp, which let a handful of cases (e.g. `case_64`-`68`) cite a file that, per the dataset's own manifest, did not exist yet as of their `audit_at`. Fixed in `code/pipeline/evidence_loader.py`: a record's effective `captured_at` is now the *later* of its own timestamp and its file's manifest timestamp, since a fact only became knowable to an auditor once the file itself was exported. Full-dataset invariant testing now enforces this with zero violations. This changed the full-run status distribution (COMPLETE 195→187, INCOMPLETE 313→321).
11. **Five more `.log` format variants never seen before**, found by investigating *why* 26 cases were `COMPLETE` with zero steps instead of assuming it was fine (see Section 3a for full detail): a bracketed `[LEVEL] [service] [trace] event_name:` format, a `message="<escaped key=val pairs>"` wrapper, a pipe-delimited format, a `requestId`-instead-of-`trace_id` correlation field, and an RFC5424 syslog header with a bare positional trace id. Fixing all five surfaced genuine, previously-hidden remaining-record violations and reduced zero-step `COMPLETE` cases from 26 to 2 (status distribution: COMPLETE 187→170, INCOMPLETE 321→338).
12. **An incorrect policy change, caught before being kept**: in response to the explicit question "does the spec permit COMPLETE with zero steps," a policy was implemented to make any zero-evidence case `INCOMPLETE` instead, reasoning from the English "sufficient evidence" wording alone. This was checked against `sample_output.csv` before being accepted and found to directly contradict labeled ground truth (`case_02`/`case_04` are the identical situation and are golden-labeled `COMPLETE`). Reverted immediately; documented here per the explicit instruction not to overstate or hide mistakes made along the way.

## 6. Custom evaluation cases (participant-created, beyond the required sample)

The sample (`sample_output.csv`) contains **zero `NOT_APPLICABLE` cases**, so it cannot validate non-deletion classification at all. The following cases, drawn from the real `deletion_requests.csv` (not organizer-only data, and manually read/verified, not a hidden label) were used to specifically test this and other edge cases:

| Case | Text (abridged) | Expected | Actual | Result |
|---|---|---|---|---|
| `case_537` | "My surname is misspelled... correct it without removing any of my records" | `NOT_APPLICABLE` (correction) | `NOT_APPLICABLE`, `authorizes_deletion=False`, category `CORRECTION` | ✅ |
| `case_542` | "send me a copy... don't delete or change anything" | `NOT_APPLICABLE` (access/export) | `NOT_APPLICABLE`, category `ACCESS_OR_EXPORT` | ✅ |
| `case_534` | "stop the promotional emails... not asking for deletion" | `NOT_APPLICABLE` (marketing opt-out) | `NOT_APPLICABLE`, category `MARKETING_OPT_OUT` | ✅ |
| `case_53` | "erase the account... apply the correct treatment..." (boilerplate trap) | authorizes deletion | `authorizes_deletion=True`, category `DELETION` | ✅ |
| `case_03` (sample) | French-language deletion request | authorizes deletion | `authorizes_deletion=True` | ✅ |
| `case_06`/`25`/`32` (sample) | Follow-ups on an *already in-progress* deletion (not phrased as "delete me") | authorizes deletion (continuation) | All three correctly `authorizes_deletion=True` after a documented prompt fix (initial run misclassified all three — see Stage 4 history) | ✅ (after fix) |
| Constructed: compliant-blocked backup | Synthetic `BACKUP_ARCHIVE` record with `state="BLOCKED"` | no restore-risk finding, only compliant verifications | 2 compliant outcomes, 0 findings | ✅ |
| Constructed: contradictory same-timestamp evidence | Two records for one resource at the same instant, one `FOUND` one `NOT_FOUND` | deterministic resolution, no crash | Resolves consistently on repeated calls | ✅ |
| Constructed: zero-evidence category | A category with no evidence anywhere, `user_resolved=True` | `INCONCLUSIVE` (the user-existence exception is USER_PROFILE/ACCOUNT_TOMBSTONE-only) | `INCONCLUSIVE` | ✅ |

## 7. Known limitations

- **Resource discovery depends on identifier survival.** A resource that was already anonymized/redacted/deleted before any surviving evidence captured an identifier for it cannot be linked back to a user from participant-visible data alone, and is correctly omitted rather than guessed (Category A above). This is the dominant source of recall loss against the sample.
- **The activity-correlation discovery fallback contributes little on the full dataset.** It was validated to help on 15/32 sample cases (0 false positives), but in the full 518-case set it never finds a safe, isolated match at all (0/518) — evidently the sample's early "clean" cases are not representative of the full set's timing patterns. It remains in place because it is risk-free (never produces a wrong attribution) but should not be expected to meaningfully raise full-dataset recall.
- **Golden answers may include organizer-only resource identities** that are, by the challenge's own rules, not derivable from participant data (Category A, Section 2) — this is an acknowledged, expected gap, not a solvable one without using disallowed information.
- Category-B findings (extra true, evidence-backed facts outside gold's per-case scope) mean the submission may show lower exact-match / precision scores on any grading dimension that penalizes "extra" output, even though that output is factually correct and evidence-backed — a deliberate tradeoff favoring truthful, grounded output over gaming the comparison.
