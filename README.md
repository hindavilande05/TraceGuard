# HackerRank Orchestrate - October 2026

## Ghost Data Still Exists

Build an AI agent that audits whether a user's personal data was actually deleted.

A deletion workflow may report **Complete** while data still exists in databases, logs, support attachments, exports, search indexes, archives, or backups. Sync and restore processes may also bring deleted data back.

For each request, the agent must first determine whether deletion was actually requested. It then returns `COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE`, reports any remaining data or recreation risk, and produces the ordered remediation and verification steps required to finish an authorized deletion safely.

Read [`problem_statement.md`](./problem_statement.md) for the complete challenge contract.

---

## This Submission's Implementation

This solution is Python, fully deterministic except for one narrow step (request-intent classification) that uses a **local** LLM via Ollama — no cloud API, no API key.

### Architecture

- **Identity resolution** (`code/pipeline/identities.py`) — resolves a request's email to a user, including historical emails/phones/hashes from `user_audit_log.csv`.
- **Evidence parsing** (`code/pipeline/evidence_sql.py`, `evidence_json.py`, `evidence_text.py`, `evidence_ocr.py`) — format-agnostic parsers for every evidence shape in the dataset (SQL snapshots, JSON records, handover reports, ~6 distinct `.log` line formats, and OCR'd PNG/PDF screenshots), all normalized into one common record shape.
- **Resource provenance** (`code/pipeline/provenance.py`) — resolves which resources belong to a user via strong identifiers only (never a bare name match), and judges whether a resource's absence from a snapshot is trustworthy proof of deletion (never from omission alone).
- **Request-intent classification** (`code/pipeline/classifier.py`) — the only step that calls a model: a local Ollama call (`qwen2.5:7b`) decides whether `request_text` actually authorizes deletion, with schema validation, disk caching, and a conservative non-model fallback.
- **State reconstruction & policy application** (`code/pipeline/state.py`, `rules.py`, `activity.py`) — applies `deletion_rules.csv`, reconstructs state as of each case's `audit_at` (never using evidence captured after it), and decides `COMPLETE`/`INCOMPLETE`/`NOT_APPLICABLE`.
- **Output** (`code/pipeline/steps.py`, `code/main.py`) — deterministic step ordering/numbering and `output.csv` generation.

### Setup

Requires Python 3.10+, [Ollama](https://ollama.com) running locally with `qwen2.5:7b` pulled, and the Tesseract OCR engine.

```bash
# 1. Install Ollama and pull the model (one-time)
#    see https://ollama.com for platform-specific install instructions
ollama pull qwen2.5:7b

# 2. Install Tesseract OCR (one-time)
brew install tesseract          # macOS
# or: apt-get install tesseract-ocr   (Debian/Ubuntu)

# 3. Create a project-local virtual environment and install Python deps
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install pytesseract PyMuPDF Pillow requests
```

No API keys or `.env` file are needed — everything runs locally.

### Running

```bash
# Ensure the Ollama server is running (usually automatic after install/pull)
ollama list   # should show qwen2.5:7b

# Run the full pipeline — reads dataset/deletion_requests.csv, writes output.csv at the repo root
./.venv/bin/python3 code/main.py
```

A first run classifies every unique `request_text` once (a few minutes); results are cached to disk (`code/.cache/llm/`, `code/.cache/ocr/`) so subsequent runs against unchanged data are near-instant and make no further model calls.

### Tests and validation

```bash
# Stage-by-stage test suites (stdlib + local deps, no network calls beyond Ollama for stage 4)
./.venv/bin/python3 code/tests/test_stage1_evidence_identity.py
./.venv/bin/python3 code/tests/test_stage2_provenance.py
./.venv/bin/python3 code/tests/test_stage3_ocr.py
./.venv/bin/python3 code/tests/test_stage4_classifier.py
./.venv/bin/python3 code/tests/test_stage5_state.py
./.venv/bin/python3 code/tests/test_stage6_full_dataset.py   # requires output.csv to already exist

# Structural validation of the generated output.csv
./.venv/bin/python3 code/tests/validate_output.py

# Compare against the 32 labeled sample cases, field by field
./.venv/bin/python3 code/tests/sample_comparison.py

# Per-case audit summary (status counts, confidence, abstentions, recreation risks)
./.venv/bin/python3 code/tests/audit_summary.py
```

See `code/evaluation/evaluation_report.md` and `code/evaluation/usage_report.md` for full results.

---

## Quick Start

Clone the repository and move into the project directory:

```bash
git clone https://github.com/interviewstreet/hackerrank-orchestrate-october26.git
cd hackerrank-orchestrate-october26
```

Start your AI coding tool from the **repository root** so it automatically reads `AGENTS.md` and logs your session turns to `log.txt` in the root directory. Both files are kept local by this author and are **not published in this repository** — the chat transcript is submitted separately through HackerRank.

Build your solution in `code/main.py`, or use another language and document its setup and run command clearly in your README.

Your solution must:

- Read the input files from `dataset/`
- Generate one prediction row for every request in `dataset/deletion_requests.csv`
- Write the final predictions to `output.csv` in the repository root

Run the starter Python entry point with:

```bash
python3 code/main.py
```

The blank template at `dataset/output.csv` is provided as a reference. Your final generated file for submission must be the root-level `output.csv`.


## Dataset

All participant-facing files are in `dataset/`:

| File | Contents |
| --- | --- |
| `deletion_requests.csv` | 518 requests |
| `sample_deletion_requests.csv` | 32 sample requests |
| `sample_output.csv` | Correct sample answers |
| `user_identities.csv` | Current user identities |
| `user_audit_log.csv` | Historical identity changes |
| `system_activity.csv` | Activity across services and data processes |
| `deletion_rules.csv` | Required treatment for each data category |
| `evidence_files.csv` | Manifest of supplied evidence files |
| `output.csv` | Blank output template |

Evidence is stored under `dataset/evidence/` and may include PDFs, images, logs, JSON, plain text, or SQL snapshot exports. SQL captures may contain a primary database, regional replicas, a reporting replica, or a failover replica so replication drift can be investigated directly. The `.sql` files are readable as text and executable with SQLite.

Only `deletion_requests.csv` requires answers. The other files provide evidence and context.

---

## Output

Create `output.csv` with these columns in this exact order:

```text
case_id,deletion_status,steps_json
```

Produce exactly one row per request. `deletion_status` is `COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE`. `steps_json` is an ordered JSON array containing findings, remediation actions, and verification checks. Use an empty array (`[]`) for `NOT_APPLICABLE`. Evidence references may be a `file_id`, `activity_id`, or `audit_id`. Determine the resource state at `audit_at` from the available evidence; reported workflow statuses and operation results are claims. Use `dataset/sample_output.csv` as the format reference.

The final file must contain 518 data rows sorted by `case_id` (plus the header row). The grader parses `steps_json` as JSON, so object-key order and whitespace do not matter; array order does. Order findings, remediation, and verification in that phase order. Within a phase, follow the operation order in the problem statement, then sort by `system_id` and `resource_id`.

---

## Evaluation

`output.csv` will be compared with hidden canonical answers. Scoring considers:

- deletion assessment accuracy;
- current and historical identity resolution;
- discovery of remaining data and recreation risks;
- correct policy application and evidence references; and
- completeness and ordering of remediation and verification steps.

Your `code.zip` must include:

```text
code/evaluation/evaluation_report.md
code/evaluation/usage_report.md
```

- `evaluation_report.md` must cover case, row, field, and sequence accuracy, at least three additional evaluation cases created by you, failures found, and improvements made.
- `usage_report.md` must cover the final full-dataset run, including model providers and names, model calls, input and output tokens, total and average tokens per request, and estimated total and per-request cost. Include per-model and overall totals when using multiple models.

**Important:** Place `evaluation_report.md` and `usage_report.md` inside `code/evaluation/` (matching `code/evaluation/main.py` in this starter layout), write your complete evaluation metrics, custom evaluation test cases, and token/cost breakdowns directly inside those Markdown files, and keep prompts in source code or Markdown files. Automated code evaluation ignores raw data files (`.csv`, `.tsv`, `.json`, `.jsonl`, `.sql`, `.log`), so content stored only in those formats will not be visible to the code judge or AI interviewer.

---

## Chat Transcript

This solution uses `AGENTS.md`, which instructs compatible AI coding tools to append conversation summaries to `log.txt` in the repository root. In this repository **both files are kept local and are not published** (see `.gitignore`); the transcript is uploaded separately as the `chat_transcript` submission.

| Platform | Path |
| --- | --- |
| macOS / Linux | `<repo root>/log.txt` |
| Windows | `<repo root>\log.txt` |

- Always launch your AI coding agent from the repository root (or explicitly ask it to read `./AGENTS.md` first) so every turn is logged to `log.txt` with the required `User Prompt`, `Agent Response Summary`, `Actions`, and `Context` sections.
- Upload `log.txt` directly as your `chat_transcript` submission on HackerRank.
- Do not include API keys, tokens, credentials, or secrets in prompts, code, configuration, or `log.txt`.

---

## Packaging `code.zip` and Submission

| Item | Description |
| --- | --- |
| `code.zip` | Runnable solution, prompts, configuration, `README.md`, and `evaluation/` reports |
| `output.csv` | One answer row for each of the 518 requests in `dataset/deletion_requests.csv` |
| `chat_transcript` | The root `log.txt` file generated by your coding agent |
| AI interview | Live explanation of your architecture, approach, and evaluation |

You may use any programming language, model, framework, or retrieval approach. Include clear setup and execution instructions in `README.md` inside `code.zip`.

To package `code.zip` from the repository root without bundling the dataset:

**macOS / Linux:**

```bash
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"
```

**Windows (PowerShell):**

```powershell
Compress-Archive -Path README.md, code -DestinationPath code.zip -Force
```

When packaging `code.zip`:

- Keep your evaluation reports in `code/evaluation/` so they are bundled automatically with `code` (or add `evaluation` to the archive command above if you placed them in a top-level `evaluation/` directory).
- Do **not** include `dataset/`, virtual environments (`.venv`, `venv`, `node_modules`), or build caches (`__pycache__`). On Windows, keep `.venv` at the repository root (outside `code/`) and remove any `code/__pycache__` folders before running `Compress-Archive`.
- Do **not** include nested archives (`.zip`, `.tar`, `.gz`, `.tgz`, `.7z`, `.rar`) inside `code.zip` — submissions containing nested archive files will be rejected.

Before submitting, confirm that:

- `output.csv` has the exact header `case_id,deletion_status,steps_json` and contains all 518 data rows sorted by `case_id`;
- every `steps_json` value is a valid JSON array with consecutive step numbers starting at `1`;
- findings cite valid evidence IDs, and `COMPLETE` verification steps cite evidence proving the current state;
- proposed remediation and future verification steps use an empty `evidence_id` (`""`);
- `code.zip` includes your runnable code, `README.md`, `code/evaluation/evaluation_report.md`, and `code/evaluation/usage_report.md`;
- `log.txt` follows the `AGENTS.md` turn format and is uploaded to the `chat_transcript` slot; and
- no API keys, credentials, real personal data, or sensitive configuration values are included.
