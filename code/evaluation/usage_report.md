# Usage Report — Ghost Data Still Exists

This report summarizes the **actual, measured** model usage for the run that produced the submitted `output.csv` (518 cases). All figures come directly from Ollama's own response fields (`prompt_eval_count`, `eval_count`, `total_duration`) recorded at call time and cached to disk per call, or from wall-clock measurements taken by the pipeline itself. Nothing here is estimated, extrapolated, or invented.

## Architecture note (why usage is this small)

Per the approved architecture, a local LLM (via Ollama) is used **only** for one narrow task: classifying whether a case's `request_text` actually authorizes deletion (`code/pipeline/classifier.py`). Every other part of the pipeline — identity resolution, evidence parsing (SQL/JSON/log/text/OCR), policy application, state reconstruction, and output generation — is deterministic Python with zero model calls. This is why the total call count below is in the hundreds, not thousands.

## Model

| Field | Value |
|---|---|
| Provider | Local (self-hosted via Ollama, `http://localhost:11434`) |
| Model | `qwen2.5:7b` (7.6B parameters, Q4_K_M quantization) |
| Model calls | 1 call per **unique** `request_text` (results are cached by `sha256(model + prompt_version + request_text)` to disk at `code/.cache/llm/`, so identical request texts across different cases are classified once) |

## Call volume

| Metric | Value |
|---|---|
| Total cases in `deletion_requests.csv` | 518 |
| Unique `request_text` values among them | 375 |
| Live model calls made to classify them | 375 |
| Cache hits (identical text already classified) | 143 (518 − 375) |
| Fallback-heuristic classifications (model unreachable/invalid response) | 0 |
| Low-confidence classifications flagged for review | 0 |

The specific run that wrote the final `output.csv` reused the disk cache from these 375 calls and made **0 additional live model calls** (0.1s wall clock, pure cache reuse). The 375 live calls above are the actual, one-time classification work that backs every case's `deletion_status` decision; they were performed once during pipeline development/testing and are reused deterministically on every subsequent run against unchanged `request_text` values, including the final run.

## Token usage (375 live calls, covering all 375 unique request texts)

| Metric | Value |
|---|---|
| Total prompt (input) tokens | 268,289 |
| Total completion (output) tokens | 16,635 |
| **Total tokens** | **284,924** |
| Average tokens per live call | 759.8 |
| Average tokens per case (284,924 ÷ 518, amortizing cache reuse) | 550.0 |

## Latency

| Metric | Value |
|---|---|
| Total model-call wall-clock time (375 live calls) | 402.0s (~6.7 minutes) |
| Average per live call | 1.07s |
| Full pipeline run that produced `output.csv` (518 cases, fully cached) | 0.2s |

## Cost

| Metric | Value |
|---|---|
| API cost | **$0.00** — the model runs locally via Ollama; there is no per-token billing. |
| Estimated cost per request | $0.00 |
| Estimated total cost | $0.00 |

No cloud API, API key, or paid inference endpoint is used anywhere in this pipeline, per the approved architecture.

## Per-model / overall totals

Only one model is used in this pipeline, so per-model and overall totals are identical:

| | qwen2.5:7b | Overall |
|---|---|---|
| Calls | 375 | 375 |
| Prompt tokens | 268,289 | 268,289 |
| Completion tokens | 16,635 | 16,635 |
| Total tokens | 284,924 | 284,924 |
| Cost | $0.00 | $0.00 |
