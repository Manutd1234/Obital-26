# Lesson Generation Harness — Architecture & Changelog

## Overview

The lesson generation system was overhauled to support background execution with real-time progress tracking, parallel LLM workers, context fallbacks, and cancellation.

---

## High-Level Architecture

```
Client (UI / API / CLI)
       │
       ▼
POST /lessons/generate ───────────────────────────┐
       │                                          │
       ▼                                          │
create_lesson_generation_job()                     │
       ├── Job record saved to JSON               │
       ├── Daemon thread spawned                  │
       └── Returns job dict immediately ◄─────────┘
              │ (background thread)
              ▼
       asyncio.run(_generate_lesson())
              ├── Collect context (local → Wikipedia → short primer)
              ├── Planner LLM call (plan 3 checkpoints)
              ├── 3x parallel Worker LLM calls (Semaphore=3)
              ├── Quality review + regenerate weak parts
              ├── Reviewer LLM call
              └── Save to AccountStore
                      │ (SSE stream polls job status)
                      ▼
              plan_step / subagent_* / artifact events
```

---

## Job Lifecycle

| Status | Meaning |
|--------|---------|
| `queued` | Job created, thread not yet started |
| `planning` | Collecting context, calling planner LLM |
| `generating` | Worker LLMs writing lesson parts (parallel) |
| `reviewing` | Quality check + reviewer LLM call |
| `saving` | Persisting lesson to AccountStore |
| `succeeded` | Lesson saved, `result` populated |
| `failed` | Unrecoverable error, `error` populated |
| `rejected` | Topic refused (too broad, no context, cheating) |
| `canceled` | User-initiated cancellation |

### Subtask Tracking

Each job has a `subtasks` array with per-step granularity:

```json
{
  "subtasks": [
    {"name": "planner", "label": "Planning lesson", "status": "succeeded", "phase": "planned", "progress": 100},
    {"name": "foundations_worker", "label": "Writing foundations", "status": "running", "phase": "calling_model", "progress": 35},
    {"name": "application_worker", "label": "Writing application", "status": "queued", "phase": "queued", "progress": 0},
    {"name": "mastery_worker", "label": "Writing mastery", "status": "queued", "phase": "queued", "progress": 0},
    {"name": "reviewer", "label": "Reviewing quiz quality", "status": "queued", "phase": "queued", "progress": 0},
    {"name": "saver", "label": "Saving lesson", "status": "queued", "phase": "queued", "progress": 0}
  ]
}
```

Subtask statuses: `queued`, `running`, `retrying`, `fallback`, `succeeded`, `failed`, `canceled`.

---

## Parallel Workers

The three lesson parts (foundations, application, mastery) are generated concurrently via `asyncio.gather()` with `asyncio.Semaphore(3)`.

Each worker (`_generate_part_worker`):
1. Attempts the LLM call (`raise_errors=True`)
2. On failure, retries once (2 attempts total)
3. On second failure, uses a local fallback template
4. Checks `_raise_if_canceled()` before and after each call
5. Reports progress via `_set_subtask()`

---

## Context Collection

Four-layer content tightening:

| Layer | What | Where |
|-------|------|-------|
| 1 | Token overlap (≥2 tokens per chunk) | `_collect_context` — drops unrelated doc chunks |
| 2 | Wikipedia relevance filter | `_collect_wikipedia_context` — only keeps snippets sharing tokens with doc content |
| 3 | LLM semantic vetting | `_refine_context` — single LLM call filters combined context for topic relevance |
| 4 | Stricter overlap check | `_topic_overlaps_context` — requires ≥40% of topic tokens to match |

Three-tier source fallback:

| Priority | Source | Trigger |
|----------|--------|---------|
| 1 | Local documents | Default |
| 2 | Wikipedia snippets | No local context OR weak token overlap |
| 3 | Short primer | No local + no Wikipedia |

When local context is weak, Wikipedia is **merged** with local context (`context_source="local+wikipedia"`) instead of replacing it. The `_refine_context` LLM call then drops any off-topic chunks from the combined set.

`context_source` is passed to workers so they can adjust depth (wikipedia/short_primer = shorter lessons, no niche claims).

---

## Cancellation

```
POST /lessons/generate/jobs/{job_id}/cancel?account_id=...
```

1. Sets job status to `canceled`
2. Marks all active/queued subtasks as `canceled`
3. `_raise_if_canceled()` checks between each LLM call
4. Workers catch `LessonGenerationCanceled` and exit cleanly
5. Thread finishes; job stays in `canceled` state (not restartable)

---

## Quality Review

After generation, `_find_part_quality_issues()` checks:
- Markdown < 240 chars
- Empty/missing quiz arrays
- Duplicate questions across parts
- Missing `expected_answer`

Weak parts are regenerated (parallel) before the final reviewer call.

---

## API

### New

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/lessons/generate/jobs/{job_id}/cancel` | Cancel a running job |

### Modified

`POST /lessons/generate` and `GET /lessons/generate/jobs/{job_id}` now include:
- `subtasks` — array of subtask status objects
- `request` — original request parameters

### SSE Events (via chat stream)

| Event | Payload | When |
|-------|---------|------|
| `plan_step` | `{id, title, status, detail}` | Job phase changes |
| `subagent_start` | `{id, name, title, status, phase, progress, detail}` | Subtask starts |
| `subagent_done` | `{id, name, title, status, phase, progress}` | Subtask completes |
| `subagent_error` | `{id, name, title, status, phase, progress, detail}` | Subtask fails/canceled |
| `artifact` | `{kind: "lesson", lesson_id, title, job_id}` | Lesson created (auto-opens fullscreen) |

---

## Agent Harness

Multi-round execution (`agent_harness.py`):

- **Simple tasks**: up to 3 rounds
- **Complex tasks** (lesson/quiz/debug/compare/analyze/generate/build): up to 5 rounds

Each round: LLM call → execute tools (max 5/round) → append results → loop. On max rounds, a follow-up call summarizes progress.

---

## Logging

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OBITAL_LOG_LEVEL` | `INFO` | Python log level |
| `OBITAL_LOG_FILE` | `backend/data/logs/backend.log` | Log file path |

### Structured Events

All lesson events use `logger.info("lesson_generation_* ...")`:

```
lesson_generation_job queued job_id=... topic=...
lesson_generation_stream job_id=... event=planning_started
lesson_generation_stream job_id=... event=context_collected chunks=12 source=local
lesson_generation_stream job_id=... event=parallel_workers_started workers=3
lesson_generation_stream job_id=... event=worker_done worker=foundations_worker questions=3
lesson_generation_stream job_id=... event=review_approved
lesson_generation_stream job_id=... event=saved lesson_id=... title=...
```

Quiz events use `logger.info("quiz_generation_stream ...")`.

---

## Key Files

| File | Purpose |
|------|---------|
| `backend/app/services/lesson_harness.py` | Job management, parallel generation, context collection, quality review |
| `backend/app/services/chat_service.py` | SSE streaming of job events to frontend |
| `backend/app/services/agent_harness.py` | Multi-round agent loop |
| `backend/app/services/quiz_service.py` | Quiz build/grade with structured logging |
| `backend/app/services/chat_tools.py` | Slash commands + `create_lesson_payload()` harness bridge |
| `backend/app/api/lesson.py` | REST API endpoints including job cancel |
| `backend/app/main.py` | Logging configuration |
| `frontend/src/hooks/useChatStream.ts` | Frontend SSE handling + artifact auto-open |
| `backend/tests/test_lesson_harness.py` | Unit tests |

---

## Tests

| Test | Verifies |
|------|----------|
| `test_rejects_too_broad_topic` | Preflight rejection for broad topics |
| `test_create_lesson_job_saves_three_markdown_parts` | Full flow with 3 checkpoints + subtask tracking |
| `test_cancel_lesson_job_marks_active_subtasks_canceled` | Cancellation propagation |
| `test_create_lesson_job_uses_short_primer_when_no_context` | Short primer fallback |
| `test_collect_context_excludes_unrelated_chunks` | Token overlap filtering drops unrelated doc chunks |
| `test_topic_overlaps_context_requires_minimum_ratio` | Stricter 40% overlap requirement |
| `test_collect_wikipedia_filters_unrelated_results` | Wikipedia results filtered by doc token overlap |
| `test_refine_context_keeps_relevant_chunks` | LLM vetting keeps relevant, drops off-topic |
| `test_refine_context_returns_original_on_empty_keep` | Empty keep list → all contexts preserved |
| `test_refine_context_skips_single_chunk` | No LLM call for 1 chunk |
| `test_refine_context_returns_original_on_error` | API failure → graceful fallback to original |
| `test_normalize_quiz_downgrades_invalid_mcq_index` | Invalid MCQ index → open question |
| `test_normalize_quiz_infers_mcq_index_from_expected_answer` | Index inference from expected_answer |
| `test_normalize_quiz_fallback_is_quiz_question_compatible` | Fallback format compatibility |
