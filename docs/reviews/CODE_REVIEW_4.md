# Obital — Full Code Review (`test-known-good`)

**Reviewed:** 2026-06-27  
**Branch:** `test-known-good`  
**Reviewer:** Claude Code (claude-sonnet-4-6)

---

## Project Overview

Obital is a personal AI study platform. It ingests PDFs/docs, builds a RAG index, and uses an LLM to generate curricula, lessons, quizzes, and chat answers. Three layers:

- **`engine/`** — offline Python RAG/quiz engine (sentence-transformers, DeepSeek/OpenAI)
- **`backend/`** — FastAPI REST/SSE API with Argon2id/AES-GCM E2EE, Postgres/SQLite, Alembic
- **`frontend/`** — React/TypeScript SPA (Vite, Zustand, SSE streaming)

---

## Pipeline End-to-End Trace

1. **Upload:** `POST /docs/upload` (`doc.py`) → limit check → extract text → encrypt with DEK → `storage.add_doc()`
2. **Embed:** `PUT /docs/{doc_id}/embeddings` (in pipeline router) → `RAGIngestionService` → `sentence-transformers` or hash fallback → JSONL/LightRAG store
3. **Generate:** `POST /pipeline/generate-curriculum` → `CurriculumQAPipeline.run()` → `HybridContextBuilder` prompt → AsyncOpenAI → structured JSON
4. **Chat:** `POST /chat/sessions/{id}/messages/stream` → `chat_engine` → `agent_harness` → DeepSeek → DSML tool-call parsing → `chat_tools` dispatch → SSE chunks
5. **Quiz:** `POST /quiz/build` → `generate_quiz()` → LLM → `normalize_quiz()` → JSON file store. `POST /quiz/grade` → semantic + lexical scoring

---

## Critical Bugs (pipeline-breaking)

### C-1: `asyncio.run()` inside an async FastAPI endpoint — `backend/app/api/quiz.py`

Every quiz build request crashes with `RuntimeError: This event loop is already running`. FastAPI already runs on an event loop; calling `asyncio.run()` from within it is illegal.

**Fix:** Declare the endpoint `async def` and `await generate_quiz(...)` directly.

---

### C-2: `asyncio.new_event_loop()` called inside a running async framework — `backend/app/api/doc.py`

`_extract_doc_tags()` creates a new event loop inside a sync function called during request handling. Causes `RuntimeError` or silent loop leakage under uvicorn.

---

### C-3: Mutable `BackgroundTasks()` default argument — `backend/app/api/doc.py`

```python
async def upload_doc(..., background_tasks: BackgroundTasks = BackgroundTasks())
```

Python evaluates defaults once at import time. Every request without an explicit `background_tasks` argument shares the same object — tasks from one request leak into all subsequent requests. This is a severe correctness bug under concurrent load.

**Fix:** Remove the default entirely; FastAPI injects `BackgroundTasks` automatically.

---

### C-4: `assert` used as a production safety guard — `backend/app/services/usage.py`, `storage.py`

`assert SessionLocal is not None` is stripped by Python's `-O` optimizer (standard in many production containers), turning the assert into a silent `None` dereference.

**Fix:** Replace with `if SessionLocal is None: raise RuntimeError(...)`.

---

### C-5: ContextVar DEK leak across concurrent daemon threads — `backend/app/services/lesson_harness.py`

`_save_generated_lesson()` calls `active_user_dek.set(user_dek)` inside a daemon thread. Threads share the default `contextvars` context in CPython. Under concurrent lesson generation for two different users, one user's DEK can overwrite the other's — **User A's lesson may be encrypted with User B's key**.

**Fix:** Pass the DEK as a local function argument instead of mutating a ContextVar from a thread.

---

### C-6: `delete_doc()` local-mode path broken for no-DB deployments — `backend/app/services/storage.py`

The filesystem delete path hits `assert SessionLocal is not None` then tries to write a `BinItemModel` row. In SQLite/no-DB mode `SessionLocal` may be `None`, so the bin record is never written and the assert fires (or silently vanishes with `-O`).

---

## Security Issues

### S-1: Default credential `postgres:postgres` in `.env.example`

Any developer copying this file without changing it exposes their database.

### S-2: `UNLOCK_TOKEN_SECRET=change-me` in `.env.example`

If not rotated, all Fernet-wrapped DEKs are trivially derivable. This secret protects all encrypted user data.

### S-3: Rate limiting is in-process only — `backend/app/api/rate_limit.py`

Module-level `_BUCKETS: dict` resets on every Vercel cold start or new worker process. Rate limits are completely ineffective in the declared serverless deployment.

### S-4: Brute-force window resets on cold starts — `backend/app/services/unlock_session.py`

Same problem as S-3 for `_token_failures`.

### S-5: Empty `User-Agent` bypasses session binding — `backend/app/services/unlock_session.py` ~line 162

```python
if row.user_agent and not secrets.compare_digest(...)
```

If `row.user_agent` is an empty string, the check is skipped entirely, allowing any client to claim a session with just the correct `X-Client-Id`.

### S-6: CORS hardcoded to localhost — `backend/app/main.py`

Only `http://localhost:5173` and `http://localhost:3000` are allowed. Production Vercel frontend is a different origin — all production API calls will be blocked by CORS.

### S-7: Chunked upload bypasses size limit — `backend/app/main.py`

`UploadSizeLimitMiddleware` only checks the `Content-Length` header. Chunked-transfer requests omit this header and bypass the limit entirely.

### S-8: `profile_json` column stored plaintext — `backend/app/services/models.py`

Quiz history, lesson progress, usage stats, and friend lists are never encrypted, contradicting the E2EE story for the rest of the data.

### S-9: `account_id` in query-string — `frontend/src/api/client.ts`

`account_id=` is appended to every URL, leaking it into server access logs and browser history. Should be derived server-side from the authenticated session.

---

## Database / Migration Issues

### D-1: No auto-migration on startup for Postgres — `backend/app/services/db.py` ~line 139

`init_db()` returns early for Postgres mode. No startup guard or CI step enforces `alembic upgrade head`. A fresh cloud deploy silently fails on the first DB query.

### D-2: Empty tombstone migration may break the chain — `backend/alembic/versions/20260623_01_compat_reverted_chunk_count.py`

Both `upgrade()` and `downgrade()` are `pass`, and it depends on revision `20260617_01` which is absent from the repo. `alembic upgrade head` on a clean DB may fail.

### D-3: `AccountArtifactModel` missing `created_at` — `backend/app/services/models.py`

Sort-by-creation is impossible without a future migration.

### D-4: `BinItemModel` missing FK to accounts — `backend/app/services/models.py`

Orphaned bin items cannot be cleaned up per-account; no DB-level isolation between accounts.

### D-5: `_text_from_bytes_cache` grows unbounded — `backend/app/services/storage.py`

Module-level `dict` with no size limit; potential OOM on long-running instances.

### D-6: Non-atomic token count update across workers — `backend/app/services/usage.py`

Thread lock protects in-process races only. In Postgres multi-worker mode, SELECT + UPDATE without row-level locking means counts can be under-reported under concurrency.

---

## API / Contract Issues

### A-1: `LessonCheckpoint.sources` type mismatch — `backend/app/schemas.py`

Schema declares `sources: list[str]` but `lesson_harness.py` stores them as `list[dict]`. Pydantic serialization will either fail validation or silently drop source metadata fields.

### A-2: `max_tokens` schema vs. enforcement mismatch

`schemas.py` says `max_tokens = 500_000`; `usage.py` enforces `MAX_TOKENS = 100_000`. The client is told a 5x higher limit than what is actually enforced.

### A-3: Auth result unused in pipeline endpoints — `backend/app/api/pipeline.py`

`account_id` and `user_dek` from `Depends(require_account)` are declared but never used. Any authenticated user can trigger resource-intensive pipeline generation with no per-user isolation.

### A-4: Quiz scores lost in serverless — `backend/app/api/quiz.py`

Quiz results write to a local JSON file via `AccountStore`. On Vercel that file is ephemeral — scores are silently lost between deployments.

### A-5: `requests` not in requirements — `backend/requirements.txt`

`web_search.py` imports `requests` but it is not listed as a dependency. Works only as a transitive dependency; any upstream change breaks it silently.

### A-6: Non-standard SSE format

Events embed the event type inside the JSON payload rather than using the standard SSE `event:` line prefix. Works only with the custom `readSSE` parser; breaks with browser `EventSource` or standard SSE tooling.

---

## Test Coverage

| Area | Status |
|------|--------|
| Auth endpoints (`/auth/*`) | No tests |
| Streaming chat endpoint | No tests |
| Quiz grading accuracy | No tests |
| E2EE for storage operations | Primitives only — no integration tests |
| Browser integration (Playwright) | Fixtures defined, no tests written |
| Pipeline generation | No tests |

- `conftest.py` defines Playwright browser fixtures but no test file uses them.
- Running `pytest` without Playwright installed fails at collection time due to unconditional imports.

---

## CI/CD Issues

### CI-1: No pytest in CI

Neither workflow runs the backend test suite. Regressions are invisible.

### CI-2: No linting or type checking

No `mypy`, `ruff`, `flake8`, or `pyright` step. The type mismatches in A-1 and A-2 go undetected.

### CI-3: CVE suppression with no expiry — `.github/workflows/package-security-ci.yml`

A known vulnerable PyTorch CVE is suppressed indefinitely with no linked issue or expiry date.

### CI-4: `vercel.json` missing Python runtime pin

No `functions` block specifying the Python version. Vercel default runtime can change between deployments causing subtle breakage.

### CI-5: No Alembic migration consistency check

No CI job runs `alembic check`. A broken migration chain (D-2) is invisible until a real deployment.

### CI-6: Author attribution workflow missing `permissions` block

`author-attribution-ci.yml` inherits the repo's default permissions (potentially `write-all`). A read-only check should declare `permissions: contents: read`.

---

## Code Quality

- **Q-1:** Dead code — `_complete_with_tools` and `_complete_with_tools_stream` in `chat_engine.py` are several hundred lines that are no longer executed (superseded by `agent_harness`).
- **Q-2:** DSML regex patterns for DeepSeek tool-call parsing are duplicated across `chat_engine.py` and `agent_harness.py`.
- **Q-3:** Multiple `AccountStore` instances created per request across `chat_tools.py`, `usage.py`, `unlock_guard.py`, `auth.py`, and `memory_service.py` — the JSON file is re-read on every hot-path call.
- **Q-4:** `_qa_cache` polling in `generation_jobs.py` uses a `while True: sleep(1)` busy-wait for up to 300 seconds. An `asyncio.Event` or `threading.Event` would eliminate the busy-wait.
- **Q-5:** `engine/config.py` is a wildcard re-export (`from ... import *`), making config symbol origins untraceable.
- **Q-6:** Chat history truncated to `past_messages[-5:]` in `chat_engine.py` — users experience non-sequitur responses in longer conversations.
- **Q-7:** `getOrCreateClientUUID()` in `authStore.ts` stores UUID in `sessionStorage`, which resets per browser tab. Token binding via `X-Client-Id` breaks when a user opens a second tab.
- **Q-8:** Hardcoded Dijkstra fallback in `lesson_harness.py` — when an LLM call fails, placeholder content about Dijkstra's algorithm is surfaced to users regardless of the requested topic.
- **Q-9:** `JsonJobStore` grows unbounded in `background_jobs.py` — no TTL cleanup; entire file loaded on every access.

---

## What Works Well

- **E2EE architecture is sound.** Argon2id KEK derivation with configurable memory/iteration parameters, AES-GCM DEK wrapping, Fernet token transport, and `require_dek` enforcement are cleanly implemented with no obvious cryptographic flaws.
- **Unlock guard exponential backoff.** `unlock_guard.py` uses `SELECT FOR UPDATE` for safe concurrent lockout increment, doubles backoff per consecutive lockout, capped at 240 minutes.
- **`HybridContextBuilder` prompt caching.** Stable system message with deterministic JSON serialization enables prompt prefix caching at the LLM provider level.
- **Pull-before-push sync.** `sync_scheduler.py` correctly pulls remote before pushing to prevent data loss races during background sync.
- **RAG retrieval modes.** Supports keyword, vector, and hybrid retrieval with configurable alpha blending and hash-embedding fallback for minimal environments.
- **Frontend SSE abort.** `useChatStream.ts` uses `AbortController` correctly on component unmount and new-message events.
- **Supply-chain CI.** `pip-audit` with hash verification on lock files + `npm audit` provides meaningful dependency security coverage.
- **Quiz normalization.** `quiz_harness.normalize_quiz()` handles MCQ/open-ended conversion, source context injection, and quality checks in a clean, testable function.

---

## Summary Verdict

**Do not ship this build to production without fixing at minimum C-1, C-3, and C-5.**

Three crash-class bugs will manifest on first use of core features:

- **C-1** breaks every quiz build with `RuntimeError: This event loop is already running`
- **C-3** causes document upload task leakage under any concurrent load
- **C-5** is a silent cross-user encryption bug — extremely difficult to diagnose in production

Rate limiting and token-failure tracking are completely inoperative in the declared Vercel serverless environment (in-process state, reset on every cold start). `profile_json` is stored plaintext, directly undermining the E2EE story.

### Recommended immediate fixes before any cloud deployment

1. Convert the quiz endpoint to `async def` and `await generate_quiz()` — fixes **C-1**
2. Remove the `BackgroundTasks()` default argument — fixes **C-3** (FastAPI injects it automatically)
3. Pass the DEK as a function argument instead of setting a ContextVar inside a thread — fixes **C-5**
4. Replace all `assert SessionLocal is not None` with `if SessionLocal is None: raise RuntimeError(...)` — fixes **C-4, C-6**
5. Move rate limit and failure tracking state to Postgres — fixes **S-3, S-4**
6. Add `pytest` to CI with auth, quiz, and E2EE storage integration tests — fixes **CI-1**
7. Fix CORS origins to include the actual production frontend URL — fixes **S-6**
8. Pin the Python runtime version in `vercel.json` — fixes **CI-4**
