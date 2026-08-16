# Backend Improvement Roadmap

This file tracks practical improvements for the document-to-curriculum backend.

## Highest Value Improvements

### 0. Quiz Router Reliability + Token Control

The quiz chatbot router now supports function tool-calling with compact context, but it should be tracked as an explicit improvement stream.

Implemented:

- `DONE`: migrated quiz intent routing from JSON-only prompting to tool-calling with strict schema.
- `DONE`: reduced router context to current question + state flags (no full quiz question dump).
- `DONE`: fallback to JSON mode if provider/model does not return tool calls.
- `DONE`: per-turn router mode/latency visibility in session output.
- `DONE`: router summary at session end (submit/quit).
- `DONE`: router telemetry export to `engine/output/router_telemetry_<username>.json`.
- `DONE`: per-quiz telemetry reset so summary is scoped to one quiz session.
- `DONE`: route-mode accuracy fix (`slash`, `tool_call`, `fallback_json`, `local_fallback`) to avoid stale telemetry display.
- `DONE`: telemetry history append file `engine/output/router_telemetry_history_<username>.json`.

Recommended next:

- Add confusion guardrails (e.g. force `/ask` when input is a clear definition question).
- Add a short router evaluation suite with expected intent outputs for common user phrases.

### 0.1 Quiz UX + Intent Accuracy

Implemented:

- `DONE`: `/dashboard` command available in and out of active quiz flow.
- `DONE`: natural-language dashboard triggers (e.g. "show me my dashboard", "my stats").
- `DONE`: in-quiz dashboard now shows live progress (`answered/total`, remaining).
- `DONE`: last-question skip flow now shows end-of-quiz prompt directly instead of reprinting the full question.
- `DONE`: quiz request parser now maps singular phrasing ("a question", "one question") to `num_questions=1`.
- `DONE`: quiz request parser supports number words (`one` ... `ten`) for question count extraction.
- `DONE`: improved topic inference for natural phrasing (e.g. "give me a hard tree question" infers topic `tree`).
- `DONE`: natural-language `skip` now maps to `/skip` (not `/next`).
- `DONE`: negative topic parsing (`not about`, `except`, `without`) now maps to `exclude_topic`.
- `DONE`: quiz builder supports `exclude_topic` filtering.
- `DONE`: when filtered candidates are fewer than requested, fallback question variants are generated to satisfy count (when possible).
- `DONE`: quiz build now exposes an explicit status indicator (`ok`, `fallback_used`, `failed`) so failures are visible in CLI output.
- `DONE`: exclusion is now strict; if exclusion removes all candidates, build returns `failed` with `failure_reason=exclude_eliminated_all`.
- `DONE`: fallback generation is bounded by ratio and per-base variant caps to avoid low-quality repetition.
- `DONE`: build now returns explicit `failure_reason` and CLI shows reason-specific guidance.

Quick verification checklist:

- In quiz, `/submit` prints route mode `slash` (not stale previous LLM mode).
- A natural-language route (for example `what is a b-tree`) prints `tool_call` or `fallback_json` mode with latency.
- `give me a hard tree question` builds `1` question and applies topic filter `tree` where available.
- End a quiz twice and confirm `router_telemetry_history_<username>.json` grows with new events.

### 1. Token Optimization

Current cheap mode reduces context and QA volume, but the pipeline still sends raw text chunks directly to DeepSeek.

Recommended improvements:

- Add `--summarize-first`: summarize each document/chunk locally or with a cheap model before curriculum generation.
- `DONE`: `--changed-only-qa` to skip unchanged QA reruns.
- `DONE`: `--max-documents` for capped test runs.
- `DONE`: `--include` / `--exclude` filters.
- Add context deduplication before prompts so repeated notes are not sent multiple times.
- `DONE`: token usage logging and summary (actual usage + estimate fallback).

Suggested defaults:

```text
cheap-mode:
  max_topics: 4
  qa_per_topic: 2
  max_global_context_chunks: 6
  max_topic_context_chunks: 3
  qa_concurrency: 2

normal-mode:
  max_topics: 12
  qa_per_topic: 5
  max_global_context_chunks: 24
  max_topic_context_chunks: 10
  qa_concurrency: 6
```

### 2. Avoid Full QA Regeneration

Currently `--run-qa` regenerates the merged curriculum + QA even if no files changed.

Recommended improvements:

- `DONE`: corpus hash is built from selected ingested file hashes.
- `DONE`: corpus hash is stored in output metadata.
- `DONE`: QA is skipped when corpus hash and QA signature match.
- `DONE`: `--force-qa` override.

This gives the biggest cost saving for repeated testing.

### 3. Better Retrieval Before Prompting

Current topic context selection supports `keyword`, `vector`, and `hybrid`.

Recommended improvements:

- `DONE`: vector similarity ranking for topic context.
- `DONE`: rank-based hybrid merge of keyword + vector.
- `DONE`: `--retrieval-top-k`, `--retrieval-mode`, `--retrieval-alpha`.
- Remove duplicate or near-duplicate chunks before prompting.

This improves quality and reduces tokens.

### 4. Safer Folder Processing

Folder mode is recursive, which is useful but can process more files than expected.

Recommended improvements:

- `DONE`: `--max-documents`.
- `DONE`: include/exclude glob filters.
- Print a preflight list of files and ask for confirmation when more than N files are found.
- `DONE`: `--dry-run`.

### 5. Output Stability

Current schema retry improves JSON validity, but output can still vary between runs.

Recommended improvements:

- Add stable temperature setting, ideally low or zero if DeepSeek supports it.
- Save prompts used for each run under ignored debug output.
- Store generation metadata:
  - model
  - timestamp
  - settings
  - corpus hash
  - prompt version

### 6. Real LightRAG Integration

The current LightRAG adapter is guarded and falls back to JSONL when LightRAG is unavailable.

Recommended improvements:

- Install and pin the exact LightRAG package/version.
- Replace the placeholder adapter with real LightRAG insert/query calls.
- Add `--backend-status` to explain why LightRAG is unavailable.
- Add tests for JSONL fallback and LightRAG mode separately.

### 7. Embedding Improvements

Current embedding uses `sentence-transformers` if available and a deterministic fallback otherwise.

Recommended improvements:

- Log whether real embeddings or fallback embeddings were used.
- Add `--embedding-backend local|fallback`.
- Persist embedding model metadata in the manifest.
- Re-ingest automatically if the embedding model changes.

### 8. Test Coverage

Recommended tests:

- `.txt`, `.md`, `.pdf` extraction.
- Recursive folder discovery.
- Cache hit/miss behavior.
- `--cheap-mode` effective settings.
- QA schema retry behavior using mocked DeepSeek responses.
- Output filename collision handling.

### 9. CLI UX

Recommended improvements:

- Add `--dry-run`.
- Add `--list-documents`.
- Add `--backend-status`.
- Add `--show-settings`.
- Add clearer final summary:
  - files processed
  - files skipped
  - chunks written
  - estimated tokens
  - output paths

## Suggested Next Implementation Order

1. Add token estimate logging before QA calls.
1. Add chunk deduplication before prompt assembly.
2. Pin and wire the real LightRAG backend.
3. Add tests around extraction, cache, CLI settings, retrieval, and QA validation.
4. Add `--backend-status` and `--show-settings` UX flags.

## Quick Wins

The fastest useful improvements are:

- backend status command
- prompt context deduplication
