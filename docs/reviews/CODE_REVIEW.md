# Obital — Comprehensive Code Review

| | |
|---|---|
| **Repository** | `Asdao/Obital` |
| **Branch / commit reviewed** | `main` @ `8bc813e` |
| **Review date** | 2026-06-09 |
| **Scope** | Full repository: backend (FastAPI), `engine` pipeline & quiz engine, both frontends (Textual TUI + React), deployment glue, tests, CI/CD, dependency management, documentation, and repository hygiene |
| **Method** | Manual reading of the core modules plus five focused deep-dive passes (services, API routers, pipeline, frontends/deploy, tests/CI/docs/hygiene). No files were modified during the review. |

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [How to read this document](#2-how-to-read-this-document)
3. [Architecture overview](#3-architecture-overview)
4. [Top findings — the short list](#4-top-findings--the-short-list)
5. [Security & authorization](#5-security--authorization)
6. [Backend services layer](#6-backend-services-layer)
7. [Backend API router layer](#7-backend-api-router-layer)
8. [`engine` pipeline & quiz engine](#8-engine-pipeline--quiz-engine)
9. [Data sync & concurrency](#9-data-sync--concurrency)
10. [Frontends](#10-frontends)
11. [Deployment & operations](#11-deployment--operations)
12. [Testing](#12-testing)
13. [CI/CD](#13-cicd)
14. [Dependency management](#14-dependency-management)
15. [Documentation](#15-documentation)
16. [Repository hygiene](#16-repository-hygiene)
17. [What is done well](#17-what-is-done-well)
18. [Prioritized remediation roadmap](#18-prioritized-remediation-roadmap)
19. [Appendix: finding index](#19-appendix-finding-index)

---

## 1. Executive summary

Obital is an ambitious and, in many places, impressively engineered learning platform: an offline RAG + quiz-generation pipeline (`engine`) feeding a FastAPI backend, with a client-side end-to-end-encryption (E2EE) model, a background cloud-sync scheduler, and two separate user interfaces. For a student/Orbital-scale project, the depth is well above average — the cryptographic envelope, the unlock-session design, the test suite for the crypto/sync layers, and the supply-chain hardening in CI are all genuinely good work.

The findings in this review are therefore mostly about **closing gaps around a strong core**, not about a broken foundation. They cluster into five themes:

1. **Authorization is the weakest pillar.** Account identity is taken from a client-supplied `account_id` query parameter (defaulting to `admin`), and the unlock token is *optional* on nearly every route. The strong crypto protects data **at rest in the cloud**; it does not control **who may call the API**. In SQLite/local mode (plaintext at rest) any caller can read or write any account's data by passing `?account_id=<victim>`. This is partially documented in `backend/README.md` as an accepted trade-off for the local single-user case, but `SECURITY.md` overstates the posture, and the model is unsafe for any shared/cloud deployment.

2. **Blocking and unauthenticated-expensive work on request paths.** Synchronous LLM calls, model inference, document re-parsing, and even `asyncio.run()` execute inline on request handlers. Several LLM-driving endpoints (`/pipeline/*`, `/quiz/build`) require no token, which turns them into cost-amplification / denial-of-service vectors against the server's `DEEPSEEK_API_KEY`.

3. **E2EE silently degrades to plaintext when the DEK is missing.** Several writers store plaintext if no data-encryption key is resolved, instead of refusing — undermining the at-rest guarantee inconsistently (some paths correctly raise; others do not).

4. **Cloud sync is destructive, full-replacement, and non-incremental.** ~~Each cycle deletes and re-inserts chat/reward rows on both ends with a push-before-pull ordering and no conflict resolution — a real data-loss risk for multi-device use.~~ **Addressed (2026-06-12).** See §9 status notes for incremental sync implementation.

5. **Repository hygiene and delivery topology need cleanup.** An ~87 MB model file, real user chat logs, IDE config, and build artifacts are committed; there are effectively **three** frontends with the React app not actually shipping; CI hardens dependencies but never runs the tests; and the backend lock file omits runtime dependencies the backend imports.

None of these are hard to address, and the remediation roadmap in §18 sequences them. The recommended first actions are: require a token + bind it to the account on all account-scoped routes (§5), stop committing the model and chat data (§16), and make the LLM endpoints authenticated and bounded (§5, §7).

**Approximate finding counts:** Critical: 7 · High: 23 · Medium: 30+ · Low/Nit: 40+. Severity reflects impact *in a deployed/cloud context*; many drop a level if Obital is only ever run as a single-user local app.

---

## 2. How to read this document

Each finding carries a **severity**, a **location** (`file:line`), a description, and a concrete recommendation.

| Severity | Meaning |
|---|---|
| **Critical** | Exploitable security hole or guaranteed data loss/corruption in a realistic deployment. Fix before any non-local use. |
| **High** | Serious correctness, security, or reliability problem; likely to bite. |
| **Medium** | Real issue with bounded blast radius, or a strong maintainability/perf concern. |
| **Low** | Minor correctness/robustness or notable code smell. |
| **Nit** | Cosmetic / stylistic. |

Cross-cutting issues (authorization, blocking work, E2EE fallback, sync) are described once in their thematic section and referenced elsewhere rather than repeated.

A note on context: severities assume the app may run **multi-user and/or in the cloud**, because the codebase clearly supports that (Postgres mode, cloud sync, Vercel deploy). If the intended deployment is strictly single-user-local, downgrade the authorization findings accordingly — but then the cloud/multi-device features should be gated or documented as experimental.

---

## 3. Architecture overview

```
                    ┌─────────────────────────────────────────┐
                    │  engine/  (source of truth)   │
                    │  - pipeline/ : extract → chunk → embed →  │
                    │                RAG → curriculum/QA (LLM)  │
                    │  - quiz/     : quiz backend, intent router│
                    │                TUI, account_system (PIN)  │
                    │  - shared/   : account paths, context     │
                    └───────────────┬───────────────────────────┘
                                    │ imported by
                                    ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  backend/app/  (FastAPI adapter)                               │
   │  api/      : chat, doc, lesson, rewards, settings, sync,       │
   │              quiz, pipeline, usage, auth, common               │
   │  services/ : chat_engine, chat_tools, storage, db, embeddings, │
   │              crypto_local, unlock_session/guard, account_sync, │
   │              sync_scheduler, usage, settings_service, ...       │
   └───────┬───────────────────────────────────────┬───────────────┘
           │ HTTP/SSE                                │ background thread
           ▼                                         ▼
   ┌───────────────┐  ┌────────────────┐   ┌───────────────────────┐
   │ Textual TUI   │  │ React app       │   │  DB (SQLite local /   │
   │ frontend/app  │  │ frontend/src    │   │  Postgres cloud)      │
   │ + CLI bridge  │  │ (NOT deployed)  │   │  + cloud sync         │
   └───────────────┘  └────────────────┘   └───────────────────────┘
                              ⚠ The actually-deployed web UI is the
                                hand-written vanilla-JS SPA in
                                backend/app/templates/index.html
```

Key architectural observations:

- **`engine` is the source of truth**; the backend is an adapter over account-scoped artifacts. The top-level flat modules in `engine/` (e.g. `rag_engine.py`) are intentional `from … import *` re-export shims over the real implementations in `pipeline/` and `quiz/` — fine, not duplication.
- **The E2EE model** derives a KEK from a passphrase via Argon2id, wraps a per-account DEK, and encrypts content with AES-GCM (`enc:v1:` envelope). Unlock sessions store only a SHA-256 of the bearer token and a Fernet-encrypted DEK with a TTL.
- **There are three frontends, not two** (see §10): the Textual TUI, the React app under `frontend/src`, and the vanilla-JS SPA in `backend/app/templates/index.html` that is what Vercel actually serves. The React app is not wired into the deploy.

---

## 4. Top findings — the short list

If only a handful of items are addressed, do these, in order:

| # | Severity | Finding | Section |
|---|---|---|---|
| 1 | Critical | Account identity is a spoofable query param; unlock token optional on nearly all routes → horizontal privilege escalation / data exposure | [§5.1](#51-account-identity-is-client-asserted-critical) |
| 2 | Critical | Unauthenticated LLM endpoints (`/pipeline/*`, `/quiz/build`) → cost-amplification / DoS on the server API key | [§5.2](#52-unauthenticated-expensive-llm-endpoints-critical) |
| 3 | Critical | ~87 MB model + real user chat logs + IDE config committed to git | [§16](#16-repository-hygiene) |
| 4 | Critical | React frontend: XSS via unsanitised markdown + token in `localStorage` + token in SSE URL | [§10.2](#102-react-frontend-frontendsrc) |
| 5 | High | E2EE silently stores plaintext when DEK is missing (inconsistent across writers) | [§5.3](#53-e2ee-degrades-to-plaintext-silently-high) |
| 6 | ~~High~~ | Cloud sync is destructive full-replacement with no conflict resolution → multi-device data loss | [§9](#9-data-sync--concurrency) |
| 7 | High | `asyncio.run()` and blocking LLM/model calls on request paths | [§6](#6-backend-services-layer), [§7](#7-backend-api-router-layer) |
| 8 | High | CORS `allow_origins=["*"]` with `allow_credentials=True` | [§5.4](#54-cors-wildcard-with-credentials-high) |
| 9 | High | CI runs dependency audits but never runs the tests, lint, type-check, or frontend build | [§13](#13-cicd) |
| 10 | High | Backend lock file omits runtime deps the backend imports (`openai`, `sentence-transformers`, `torch`, `pypdf`, `python-dotenv`) | [§14](#14-dependency-management) |

---

## 5. Security & authorization

This is the most important section. The cryptography is strong; the **access control around it is not**.

### 5.1 Account identity is client-asserted (Critical)

**Location:** `backend/app/api/common.py:17-36` and every router that depends on it.

`valid_account_id` reads `account_id` from a query parameter with a default of `"admin"`, validates only its *format*, and returns it. `optional_user_dek` returns `None` (not a 401) when no `X-Unlock-Token` header is present. Almost every read/write route depends on this pair, so:

- A request with `?account_id=<victim>` and **no token** operates as that account. For any data that is not E2EE-encrypted (i.e. SQLite/local mode, the documented default for development, where data is plaintext at rest), this is **full horizontal read/write access** to any account.
- Identity defaulting to `admin` means a forgotten parameter silently targets a privileged-sounding account.

The DEK gates **decryption**, not **access** or **writes**. So even in Postgres/E2EE mode, write-only and metering endpoints remain abusable (see §5.2 and the `/usage/tokens` finding in §7).

`backend/README.md:175,184` is candid that this is the behavior ("well-formed IDs are accepted… will not raise 404"), suggesting it is an accepted trade-off for the local single-user case. But the same code is what runs in cloud mode, and `SECURITY.md` does not disclose it (see §15).

**The one router that does it right** is `sync.py`: it requires a token and `unlock_session.resolve_dek` enforces `row.account_id == account_id` and `unlock_version`. A token therefore cannot be replayed against a different account or after a re-key.

**Recommendation (P0):** Introduce a single dependency — e.g. `require_account()` — that mandates a valid unlock token and returns the **token-bound** `account_id` (from `row.account_id`), and use it on every account-scoped route. Make `account_id` non-defaulting where it remains a parameter, and assert equality with the token's account. Treat `sync.py`/`resolve_dek` as the reference pattern. If single-user-local is a supported mode, gate the unauthenticated behavior behind an explicit `OBITAL_LOCAL_SINGLE_USER=1` flag so it cannot be the default in a cloud deploy.

### 5.2 Unauthenticated, expensive LLM endpoints (Critical)

**Locations:** `backend/app/api/pipeline.py:131-158,161-195` (`generate-curriculum`, `generate-topic-qa`); `backend/app/api/quiz.py:87,99-106` (`/quiz/build` cache-miss → `_try_generate_qa`).

These endpoints drive full LLM pipeline runs using the server's `DEEPSEEK_API_KEY`, with a spoofable `account_id`, an optional/unused token, and **unbounded** request bodies (`global_context`, `context_chunks`, `curriculum_qa: dict[str, Any]`, `max_topics`, `qa_per_topic` — `pipeline.py:79-98`). An anonymous caller can:

- Spend the project's LLM budget at will (cost amplification).
- Tie up threadpool workers for minutes per call (no timeout) → service DoS.
- In the non-stateless branch, `save_pipeline_result` (`pipeline.py:222-232`) writes to `account_qa_output_path(...)` for the spoofable account with **no token**, i.e. overwrite any account's QA artifact on disk.

`pipeline.py:152,189` also reach into underscore-prefixed internals (`_generate_curriculum`, `_generate_topic_qa`), bypassing whatever the public entry point validates, and `pipeline.py:158,195,240` leak the raw exception string into the HTTP 500 body.

**Recommendation (P0):** Require a valid token, bound the account, add rate-limiting, hard-cap all list/dict sizes and `max_topics`/`qa_per_topic`, run generation as a background job with a timeout, call public pipeline entry points, and return generic error messages (log `{exc}` server-side only).

> **Status (2026-06-10):** Partially addressed. `require_account` already mandates a valid token in non-local mode and binds the account. Rate-limiting added (2 req/min pipeline, 5 req/min quiz build). Pydantic `Field(ge/le/max_length)` bounds added to `GenerateCurriculumRequest` and `GenerateTopicQARequest`. `asyncio.wait_for` timeouts added (120s curriculum, 90s topic QA, 180s auto-generate). Exception strings sanitized in all pipeline error handlers. `_try_generate_qa` replaced `asyncio.run()` with `_sync_run_async()` that handles both sync and async caller contexts. Remaining: the endpoints still call `_generate_curriculum`/`_generate_topic_qa` (underscore internals) rather than a public entry point; this is low risk given the authenticated + bounded + rate-limited context, but could be cleaned up by exposing a public `generate_curriculum_only()` method on the pipeline class.

> **Status (2026-06-12):** Pipeline public methods added. `CurriculumQAPipeline` now exposes `generate_curriculum()` and `generate_topic_qa()` public methods. `pipeline.py` endpoints updated to call public methods. Auto-QA generation (`_try_generate_qa`) now runs in a daemon thread with an in-memory cache, non-blocking the request path.

### 5.3 E2EE degrades to plaintext silently (High)

**Locations:** `chat_tools.py:589-617` (`_save_qa_to_db`), `storage.py:727,804` (message text), `account_sync.py:287,439,477` (chat/reward push), vs. the correct behavior in `storage.py` `add_doc`/`save_embeddings` which **raise** `ValueError` when `user_dek is None` outside SQLite mode.

Multiple writers use the pattern `encrypt(dek, x) if dek else x` — storing plaintext when no DEK is resolved. `crypto_local.decrypt_text` (`crypto_local.py:69-75`) then passes through any value lacking the `enc:v1:` prefix, so the gap is invisible at read time. The net effect: encryption-at-rest can silently lapse for QA artifacts, chat messages, and synced reward notes, and a downgrade attack (stripping the prefix) yields plaintext acceptance.

> **Status (2026-06-12):** Fixed. All write-side E2EE bypasses resolved. `storage.py:append_message` and `append_reward_session_log` now use `is_sqlite_mode()` guard (plaintext only in local mode; `require_dek` raises in non-SQLite mode). `account_sync.py:_write_chat_sessions_to_db` and `_write_reward_sessions_to_db` use `user_dek is None` guard (plaintext for pull-to-local path; encrypt for push-to-cloud path). `chat_tools.py:_save_qa_to_db` removed silent `return` on `user_dek is None`; `require_dek` now enforces. `unlock_guard.py` and `account_sync.py` `print()` calls replaced with `logger.warning()`.

**Recommendation (High):** Choose one policy — **raise** when a DEK is required and absent — and apply it uniformly across all writers. Keep the prefix-based passthrough only for an explicit, time-boxed migration window, documented in the threat model.

### 5.4 CORS wildcard with credentials (High)

**Location:** `backend/app/main.py:47-53` — `allow_origins=["*"]` together with `allow_credentials=True`.

This combination is rejected by browsers per the Fetch spec when credentials are involved, and Starlette reflects the request origin when credentials are enabled, effectively allowing any origin. Because the SPA is served same-origin by FastAPI itself, broad CORS is unnecessary. Auth here is header-based (not cookies), so the practical CSRF risk is lower — but the configuration is still wrong and should be tightened for any deployed instance.

**Recommendation (High):** Restrict `allow_origins` to known frontend origin(s) via env var, or drop CORS entirely for the same-origin case; reconsider `allow_credentials`.

> **Status (2026-06-10):** Fixed. `allow_origins` is now read from `CORS_ORIGINS` env var (default `http://localhost:5173,http://localhost:3000`). `allow_credentials=False`.

### 5.5 PIN authentication weaknesses (High)

**Locations:** `engine/quiz/account_system.py:67-85,684-701`; `backend/app/api/auth.py:175-183` (`/auth/login`).

- The PIN-hash primitives are correct (PBKDF2-HMAC-SHA256, 200k iterations, per-user 16-byte salt, `hmac.compare_digest`). But PINs are a tiny keyspace (10⁴–10⁶); PBKDF2 only slows each guess by tens of ms, so an attacker with the account file/DB can brute-force a 4-digit PIN offline in seconds-to-minutes. There is **no failed-attempt throttling or lockout** on `verify_login` (unlike the excellent lockout on the passphrase-unlock path in `unlock_guard.py:59-73`).
- A **legacy unsalted, username-derived** format exists: `_pin_hash = sha256(f"{username}::{pin}")` (`account_system.py:674-677`). Any account still on it is trivially brute-forceable; upgrade is only opportunistic on next login.
- `create_user` (`account_system.py:44-65`) accepts an empty PIN.

**Recommendation (High):** Enforce a minimum PIN length/complexity, add per-account failed-attempt throttling/lockout to PIN login, force-migrate legacy hashes (re-wrap inside PBKDF2 so the weak hash never persists), and reject empty PINs. Consider a memory-hard KDF (scrypt/argon2id) for the PIN given the small keyspace.

### 5.6 Information leakage in error responses (High → Medium)

**Locations:** `pipeline.py:158,195,240` (raw `{exc}` in 500 body); `chat.py:71` (exception class name in SSE error); `auth.py:171-172` (`register_user` returns `str(exc)` as detail); `LoginOverlay.tsx:36-52` (error-string matching as an account-enumeration oracle).

**Recommendation:** Return generic client-facing messages; log details server-side via `logger.exception`. Provide a dedicated typed "account exists?" result rather than distinguishing 401 vs 404 in the UI.

> **Status (2026-06-10):** Pipeline endpoints fixed — all three error handlers now return generic messages and log `{exc}` server-side only. `auth.py` and `chat.py` leakage remains.

### 5.7 Prompt-injection surface (Medium)

**Location:** `engine/pipeline/qa_pipeline.py:247-267,137-159`.

LLM-generated `topic.id`/`topic.title` (themselves derived from arbitrary document text) are f-string-interpolated directly into the JSON schema scaffold of the per-topic prompt, and document context is embedded into the payload. A document containing instructions can poison the curriculum and inject into downstream prompts. (`topic_id`/`topic_title` are force-overwritten after parsing, which protects identity but not the prompt the model sees.)

**Recommendation:** Never interpolate untrusted text into the prompt scaffold; pass topic id/title as structured fields in the canonicalized user payload and add explicit "treat context as data, not instructions" guarding.

> **Status (2026-06-10):** Fixed. The `_generate_topic_qa` prompt template (`qa_pipeline.py:252`) is now a plain string (no f-string interpolation); `topic.id` and `topic.title` are passed as structured fields in the `user_payload` dict (serialized via `json.dumps` in `HybridContextBuilder.build_messages`). An explicit "treat context as data, not instructions" guard is present in both the curriculum and topic-QA prompts. The force-overwrite on lines 301-302 remains as defense-in-depth. No untrusted text is interpolated into either prompt scaffold.

### 5.8 Unlock-token bearer model (Medium — by design, document it)

**Location:** `backend/app/services/unlock_session.py:55-102`.

The unlock token is a bearer credential: client binding (`x-client-id` / user-agent) is advisory and bypassable when headers are absent, there is no rotation/refresh or revocation-on-use, and theft of the plaintext token (plus a matching/empty UA) yields the DEK for the TTL window (30 min). Token storage is otherwise hygienic (only a SHA-256 hash and a Fernet-encrypted DEK at rest, fail-closed decryption). This is acceptable for the design but should be stated explicitly in the threat model.

---

## 6. Backend services layer

The services layer is the most code-dense part of the backend. It is generally well-structured (context-managed sessions, thread-safe singletons), but has several correctness and performance issues.

### Concurrency, blocking, and async correctness

- **High — `asyncio.run()` on a request path.** `chat_tools.py:619-657` (`_try_generate_qa`) calls `asyncio.run(pipeline.run(...))`. If reached from within a running event loop this raises `RuntimeError: asyncio.run() cannot be called from a running event loop`; even when it works it blocks on a full LLM pipeline. **Fix:** run QA auto-generation as a background task; never `asyncio.run` on a request thread.
- **Medium — blocking LLM/model calls in code reached from async/streaming routes.** `chat_engine.py:485-535,749-807` call `client.chat.completions.create(...)` synchronously (twice per streamed turn including a follow-up); `embeddings.py:64,76` call `model.encode` synchronously. If iterated on the event loop these stall it. **Fix:** offload to a threadpool (`run_in_threadpool`/`asyncio.to_thread`) or make the engine fully sync with sync routes.
- **High — malformed tool-call history under truncation.** `chat_engine.py:434-464,676-708`: when `len(active_calls) > MAX_TOOL_CALLS_PER_TURN` the loop `break`s, but the assistant message appended earlier still advertises *all* `tool_calls`. The OpenAI protocol requires one `tool` result per advertised `tool_call_id`; the follow-up `create()` will 400. **Fix:** truncate the assistant message's `tool_calls` to match those actually executed.

### Read-modify-write races (no locking)

- **High — usage counters.** `usage.py:53-67`: `record_token_usage`/`record_doc_upload` do `get_usage` → mutate → `_save_usage`, where `_save_usage` reloads the whole `accounts.json`, patches one user, and rewrites. Concurrent requests and the sync thread interleave → lost updates / clobbered unrelated users. **Fix:** atomic increments under a lock, or move usage into the DB.
- **High — naive-datetime crash in `get_usage`.** `usage.py:24`: `datetime.fromisoformat(reset_at)` can yield a naive datetime; `datetime.now(UTC) - last_reset` then raises `TypeError`. `get_usage` runs on every chat turn and every limit check. **Fix:** coerce to UTC if naive.
- **Low — storage-byte limit never enforced.** `usage.py:90-102`: `check_doc_limit` reads `storage_bytes`, but `record_doc_upload` only increments `doc_count`, so the byte limit always compares against 0. Dead guard.

### Storage & performance

- **High — N+1 in `get_doc_texts_for_chunk_ids`.** `storage.py:594-625`: for each `doc_id` it calls `get_doc`, each of which runs a full artifact query plus `_attach_embeddings`. Selecting from K docs = K×(2 near-full queries). **Fix:** load all docs once via `list_docs` and index by id.
- **Medium — re-extraction on every chat message.** `storage.py:262-301`: `list_docs(include_content=True)` re-parses and re-chunks every file on disk, and in stateless mode writes each document to a temp file to parse (`_extract_text_from_bytes`), on the auto-search path for *every* chat message. **Fix:** cache extracted text/embeddings keyed by `(path, mtime, size)`; parse from in-memory buffers where the parser supports it.
- **Medium — SQLite append can silently drop a message.** `storage.py:771-816`: per-session `seq_no` uses `max(seq_no)+1` + unique constraint + bounded `IntegrityError` retry (×3). Under SQLite there is no row lock, and `append_message`'s return value is ignored by the caller, so exhausting retries drops a message silently. **Fix:** log when all retries fail; consider a per-session lock.
- **Low — pure-Python cosine over all chunks per query.** `storage.py:1026-1052`: O(docs×chunks×dim) per message, recomputing norms each call. Fine at small scale; note for growth.

### Other service findings

- **Medium — settings decrypt failure returns ciphertext as the prompt.** `settings_service.py:166-172`: a swallowed decrypt failure leaves `content_bytes` as raw ciphertext, then `.decode("utf-8")` either raises or returns garbage as the system prompt. **Fix:** fall back explicitly to `DEFAULT_SYSTEM_PROMPT` on decrypt failure.
- **Medium — embedding dimension hardcoded.** `embeddings.py:18-19,46`: `_EMBEDDING_DIM=384` is assumed while the model name is env-configurable; a different model's vectors mismatch and `search_chunks_by_vector`'s `zip` silently truncates → wrong similarity. **Fix:** derive the dim from the loaded model; validate stored vector lengths.
- **Medium — two DB engines to the same DB in Postgres-only mode.** `db.py:83-90`: `engine` and `_cloud_engine` are separate pools; when `EFFECTIVE_DATABASE_URL == DATABASE_URL`, "push/pull to cloud" operate on the same DB (self-sync). **Fix:** assert the Postgres self-sync path is never scheduled; add `pool_pre_ping=True`.
- **Low — dead code & order assumptions.** `chat_engine.py:515-519` (unreachable tuple branch → token count always 0 on the non-stream path), `chat_engine.py:507,782` (history insertion assumes `messages[0]` is the system message), `chat_tools.py:378` (unused `store`), `chat_tools.py:53-60` (unreachable mcq branch).
- **Low — `quiz_service` ignores `num_questions`.** `quiz_service.py:35,46-51`: the checkpoint's configured question count is read but not passed to `QuizBuildRequest`, so the builder default is used. Likely a real bug.
- **Nit — joke/noise comments.** `chat_tools.py:1,11,23` ("remind me to never touch the crypte :<", etc.) should be removed for production.

**Cross-cutting (services):** the recurring "plaintext when DEK missing" gap (§5.3), blocking work on async paths, and unlocked read-modify-write are the three themes to prioritize here.

---

## 7. Backend API router layer

The dominant theme is the authorization model already covered in §5.1. Beyond that:

- **High — several routes have no token dependency at all.** `lesson.py:62-66,100-104,114-119` (`start_lesson`, `restart_lesson`, `record_attempt`), `rewards.py:10-21` (`dashboard`, `start_reward_session`), `usage.py:20-23,37-43` (`GET /usage`, `POST /usage/tokens`). The last is notable: `POST /usage/tokens?account_id=<victim>` lets an attacker exhaust a victim's quota, and the check-then-record is a non-atomic TOCTOU.
- **High — unbounded upload read into memory.** `doc.py:114-118`: `content = await file.read()` buffers the entire upload *before* `check_doc_limit` runs. **Fix:** enforce a max body size at the ASGI layer and/or stream with an incremental cap.

> **Status (2026-06-12):** Fixed. Added `UploadSizeLimitMiddleware` (checks `Content-Length` header, default 20 MB, configurable via `OBITAL_MAX_UPLOAD_MB`). Added defense-in-depth streaming chunked read with incremental size cap in `doc.py`.
- **Medium — no pagination on `GET /docs` / `GET /lessons`.** Unbounded payloads for large accounts. Add `limit`/`offset`.

> **Status (2026-06-12):** Fixed. `GET /docs` and `GET /lessons` now accept `limit` (1–100, default 20) and `offset` (≥0) query parameters.
- **Medium — synchronous embedding on upload.** `doc.py:128-143`: `embed_texts` runs inline on the request (and isn't awaited though the route is `async`). **Fix:** background task.

> **Status (2026-06-12):** Fixed. Embedding generation now runs via FastAPI `BackgroundTasks` after the upload response is returned. Uses actual chunk IDs from `doc.chunks`.
- **Medium — `KeyError` → 500 in quiz grading.** `quiz.py:109-127`: `a["question_id"]`/`a["answer"]` on a raw `list[dict]` raise 500 instead of 422. **Fix:** use a typed Pydantic body model.

> **Status (2026-06-12):** Fixed. `GradeQuizRequest.answers` is now `list[QuizSubmissionAnswer]` — returns `422` with field-level errors on missing keys.
- **Medium — sync routes hold a DB session for the whole transfer.** `sync.py:44-88`: `push/pull_account_*` run synchronously while a `SessionLocal()` is open for the entire network transfer. **Fix:** resolve the DEK, close the session, then transfer; or move to a background job.
- **Low — inconsistent status-code mapping.** `RuntimeError` → 400 vs 404 vs 409 across `lesson`/`sync`/`quiz` with no consistent semantics.

> **Status (2026-06-12):** Partially fixed. Added `handle_user_error(exc, not_found=False)` helper in `common.py` for consistent `RuntimeError` → `HTTPException` mapping (400 for business-rule violations, 404 for not-found). Applied to `lesson.py` routes (`start_lesson`, `restart_lesson`, `record_attempt`). `sync.py` and `quiz.py` retain their current mappings (409 for sync conflicts, varied for quiz).
- **Low — `restart_lesson` returns hardcoded `current_checkpoint_index: 0`.** `lesson.py:111` ignores store state; if restart doesn't actually zero the index the response lies.

> **Status (2026-06-12):** Fixed. `restart_lesson` now returns the actual `current_checkpoint_index` from the account store.
- **High — two inconsistent account-id validators.** `common.py:11` (`^[a-zA-Z0-9_-]{3,64}$`, uppercase, min 3) vs `account_paths.py:7` (`^[a-z0-9][a-z0-9_-]{1,63}$`, lowercase, min 2). `valid_account_id` accepts inputs that `normalize_account_id` then rejects with a `RuntimeError`. **Fix:** one source of truth.

> **Status (2026-06-12):** Fixed. Removed `_ACCOUNT_ID_RE` regex from `common.py`. `_validate_account_id_format` now delegates directly to `normalize_account_id()` — single source of truth. Invalid IDs return `422`.

**Done right:** `sync.py` is the reference authorization pattern; input bounds where present (`top_k` `ge=1,le=50`, `threshold` `0..1`, `limit` `ge=1,le=100`, the `query_vector` dimension validator) are precise; vectors are stripped from responses; grading/embedding stay server-side; the SSE generator guarantees a terminal `done` via `finally`.

---

## 8. `engine` pipeline & quiz engine

The pipeline is thoughtfully built (schema-validated LLM output with bounded retry, content-hash incremental ingestion, bounded-concurrency QA generation, a cache-friendly prompt builder). The issues:

### Model loading & retrieval

- **High — the embedding model is loaded in 4 independent places** with inconsistent settings: `rag_engine.py:169-186`, `qa_pipeline.py` `_build_local_embedder`, `quiz_backend.py` `_EMBEDDER`, `quiz_tui.py` `TempVectorMemory`. A full run loads `all-MiniLM-L6-v2` 3–4× (hundreds of MB each). **Fix:** one process-wide cached loader (e.g. `functools.lru_cache` by model name).

> **Status (2026-06-12):** Fixed. New `engine/shared/embedding_loader.py` provides a process-wide `get_embedding_model()` via `lru_cache(maxsize=1)` and a `build_embed_fn(model_name)` helper returning `(embed_fn, dim, used_fallback)`. All 4 load sites (`rag_engine.py`, `qa_pipeline.py`, `quiz_backend.py`, `quiz_tui.py`) now call `build_embed_fn()` instead of constructing their own `SentenceTransformer`. Removed duplicate `_fallback_embed` from `rag_engine.py` and `qa_pipeline.py`.

- **High — `rag_engine` ignores offline settings.** `rag_engine.py:173`: `SentenceTransformer(self.embedding_model)` without `cache_folder`/`local_files_only`, unlike the quiz modules — it will attempt a network download and use the default HF cache, breaking the offline guarantee, then silently fall back to the meaningless hash embedder if offline. **Fix:** pass `cache_folder` + `local_files_only` at all load sites.

> **Status (2026-06-12):** Fixed. `build_embed_fn()` resolves `cache_folder` from `OBITAL_MODEL_CACHE_DIR` env var (or `__file__`-relative default) and sets `local_files_only=True` when the model directory exists on disk.

- **Medium — incomplete cache key.** `rag_engine.py:92-104`: the ingestion skip decision checks `sha256`/`backend`/`chunk_hashes` but **not** the embedding model or chunk params, so switching `--embedding-model` won't invalidate cached vectors, mixing dimensions in `vector_index.jsonl`. **Fix:** include `embedding_model`/`chunk_size`/`overlap` in the key.

> **Status (2026-06-12):** Fixed. Manifest entries and skip-check now include `embedding_model`, `chunk_size`, and `chunk_overlap`. Changing any of these triggers a full re-ingest.

- **Medium — character-offset chunking.** `rag_engine.py:254-265`: chunks cut words/sentences mid-token, degrading embeddings and LLM context. **Fix:** snap to whitespace/sentence boundaries.

> **Status (2026-06-12):** Fixed. `_chunk_text()` now snaps forward to the next whitespace within a ±50-char tolerance window to avoid cutting mid-word. Degrades gracefully when no whitespace is found in the tolerance.

- **Medium — silent fallback to hash embedder.** `rag_engine.py:180-186,241-251`: produces semantically meaningless vectors with no signal in the output metadata. **Fix:** record the fallback in `IngestionResult`/metadata or fail strictly.

> **Status (2026-06-12):** Fixed. `IngestionResult` now includes `embedding_fallback_used: bool = False`. Set to `True` when `build_embed_fn()` falls back to the hash-based embedder. Logged as a warning.

### LLM call handling

- **Medium — no retry/backoff on transient API errors.** `qa_pipeline.py:168-173,278-283`: only `JSONDecodeError`/`ValidationError` are retried; a transient `RateLimitError`/`APIConnectionError` aborts the run, and `asyncio.gather` cancels siblings. **Fix:** bounded exponential backoff on transient OpenAI exceptions; `return_exceptions=True` to salvage partial results.

> **Status (2026-06-12):** Fixed. `_create_with_retry()` method added to `CurriculumQAPipeline` with configurable `max_transient_retries` (default 3) and exponential backoff (1s base, 30s max) on `RateLimitError`, `APIConnectionError`, `APITimeoutError`. `asyncio.gather` now uses `return_exceptions=True`; failed topics are logged and excluded from results.

- **Medium — no request timeout.** `qa_pipeline.py:168,278`: unlike `quiz_tui.py`, these calls have no `timeout`; a hung connection blocks indefinitely.

> **Status (2026-06-12):** Fixed. All LLM calls now pass `timeout=self.llm_timeout` (default 60s, configurable via constructor param).

- **Low — retry conversation grows unboundedly** re-sending failed output (`qa_pipeline.py:187-201,299-313`).

> **Status (2026-06-12):** Fixed. Schema retry now replaces the last assistant+user pair rather than appending, keeping the conversation bounded to 2 messages per retry cycle.

- **Low — verify `response_format={"type":"json_object"}` support** and the default model id `deepseek-v4-flash` (`config.py:32`) — if it is a placeholder, every default-config run fails at first call.

> **Status (2026-06-12):** Fixed. Default model changed from `deepseek-v4-flash` to `deepseek-chat`.

### Account system & I/O robustness

- See §5.5 for PIN findings. Additionally:
- **Medium — full-store rewrite with no atomicity or locking.** `account_system.py:502-557`: every points/lesson mutation serializes and rewrites the whole file (or loops per-user in DB mode) with no temp-file+rename and no lock → O(N) writes and lost-update/corruption risk. **Fix:** upsert only the changed user; atomic write; file lock.

> **Status (2026-06-12):** Fixed. File-based `_save()` now writes to a temp file via `tempfile.mkstemp` + `os.replace()` for atomicity.

- **Medium — plaintext account file with default permissions** (`account_system.py:557`): no `0o600`, no atomic write.

> **Status (2026-06-12):** Fixed. After atomic write, `os.chmod(path, 0o600)` is applied on non-Windows platforms.

- **Medium — `.txt`/`.md` read as UTF-8 with no error handling** (`extract_text.py:18`) and PDF extraction without per-file isolation (`extract_text.py:28-33`); one bad file aborts the whole batch ingest (`main.py:401-423`). **Fix:** `errors="replace"` + per-file try/except, continue on failure.

> **Status (2026-06-12):** Fixed. `extract_text.py` now uses `errors="replace"` for UTF-8 reads. `_ingest_documents()` in `main.py` wraps each file in try/except, logs errors, and continues with remaining files.

- **Medium — quiz answers + expected answers sent to DeepSeek** in the TUI tutor/router (`quiz_tui.py:463-476,589-637`) — a data-egress path for an "offline" product. Document/gate behind an explicit online flag.

> **Status (2026-06-12):** Fixed. Both `_ask_with_llm()` and the mistake review handler are gated behind `OBITAL_ALLOW_ONLINE_TUTOR=1` env var (default off). Quiz data is only sent to DeepSeek when explicitly opted in.

- **Low — `route()` indexes `quiz.questions[current_index]` without a bounds check** (`quiz_tui.py:162`); empty quizzes (valid failed builds) raise `IndexError`.

> **Status (2026-06-12):** Fixed. `route()` now returns `None` for empty quizzes or out-of-range `current_index`.

- **Low — intent parsing brittleness:** `_extract_num_questions` grabs the first 1–2 digit number anywhere (`quiz_intent.py:88-92`); greedy topic regex captures trailing difficulty/mode words (`quiz_intent.py:72-85`); NL command detection is substring-based (`quiz_tui.py:726-780`, so "quit" inside an answer can route to `/quit`).

> **Status (2026-06-12):** Fixed. Topic regex narrowed to require a quiz-intent keyword prefix (removed bare `on/about` fallback). Number extraction now searches near question-related keywords first. NL command detection uses `re.search(rf"\b{token}\b", ...)` word-boundary checks instead of substring matching.

- **Low — `MODEL_CACHE_DIR` resolved relative to CWD** (`model_setup.py:7`); running from another directory silently uses a different/empty cache.

> **Status (2026-06-12):** Fixed. `MODEL_CACHE_DIR` resolved from `__file__` (package-relative) with `OBITAL_MODEL_CACHE_DIR` env var override.

**Done well:** `HybridContextBuilder` (stable fingerprinted prefix + canonicalized JSON for prompt-cache hits), constant-time PIN comparison with salted PBKDF2, defensive JSONL/JSON readers that skip malformed lines, schema-validated LLM output with corrective re-prompting, content-hash incremental ingestion, account-scoped path isolation with a strict regex, and bounded-concurrency async QA generation.

---

## 9. Data sync & concurrency

**High — destructive, non-incremental, push-before-pull sync.** `sync_scheduler.py:63-103` + `account_sync.py:266-299` (`_write_chat_sessions_to_db`) delete and re-insert all chat/reward rows on each side every cycle, push **before** pull, with no per-message merge, no timestamp/`updated_at` conflict resolution, and no transaction spanning the two halves. Consequences:

- If the cloud holds data the local copy lacks (e.g. from another device), push **deletes it from the cloud** before pull can merge it → last-writer-clobbers, multi-device data loss.
- A crash between push and pull leaves divergent state.
- Heavy churn (full delete+reinsert) every interval.

> **Status (2026-06-12):** Partially addressed. Sync write functions (`_write_chat_sessions_to_db`, `_write_reward_sessions_to_db`, `_write_reward_sessions_from_files`) now use upsert logic: insert new rows, update existing rows, leave untouched rows alone. `updated_at` columns added to `ChatSessionModel`, `ChatMessageModel`, `RewardSessionModel`, `RewardEventModel` with Alembic migration `20260612_01`. Remaining: push-before-pull ordering still exists (push deletes stale rows from cloud before pull can merge them). True incremental sync with per-row conflict resolution is not yet implemented.

> **Status (2026-06-12, incremental sync):** Resolved. Three changes applied:
> 1. **Pull-before-push ordering** (`sync_scheduler.py`): the scheduler now pulls cloud data first, then pushes local data. This ensures cloud data from other devices is merged locally before local changes are pushed up, preventing the push from clobbering cloud data that hasn't been seen yet.
> 2. **`updated_at`-based conflict resolution** (`account_sync.py`): `_write_chat_sessions_to_db` and `_write_reward_sessions_to_db` now compare `updated_at` timestamps and skip sessions where the incoming data is not newer than the existing row. `_merge_chat_sessions` and `_merge_reward_sessions` perform two-way merge: newest parent wins, messages/events from the other side are appended (additive merge).
> 3. **Diff-based child insert**: `_write_chat_sessions_to_db` no longer deletes all messages and re-inserts them. It diffs incoming `seq_no` against existing `seq_no` and only inserts new messages. `_write_reward_sessions_to_db` still deletes-and-reinserts reward events (no natural unique key for per-event diff), but is guarded by the parent `updated_at` check so it only runs when the session actually changed.
>
> **Remaining**: filesystem-mode pull does not persist `updated_at` to the JSON session file, so merge is effectively one-directional for that mode (cloud wins). True per-message content-based merge for concurrent edits at the same `seq_no` position is not implemented (accepted trade-off for append-only chat messages). `RewardEventModel` has no natural unique key, so event-level dedup uses timestamp.

**Recommendation (High):** ~~Move to incremental sync keyed by `updated_at` with explicit conflict resolution (newest-wins or per-message merge), and wrap each account's sync in a single logical operation. At minimum, pull-then-merge before any destructive push.~~ Addressed. See status notes above.

Supporting findings: `sync_scheduler.py:47-58` reads the interval once at import and doesn't guard against negative values (busy-loop risk); `account_sync.py:887,893` use `print` instead of the logger; `account_sync.py:464` aborts a whole account's sync on one corrupt JSONL line; cross-session row usage (`account_sync.py:599-610`) relies on eagerly-loaded scalars and would break if a relationship is added (use `expire_on_commit=False` or materialize tuples).

> **Status (2026-06-12, supporting findings):**
> - Negative interval guard: **Fixed.** `sync_scheduler.py` now validates `SYNC_INTERVAL_MINUTES` at import; negative values log a warning and default to 15 minutes.
> - Jitter: **Fixed.** Each sync cycle adds 0–30s random jitter to prevent thundering herd.
> - `print()` calls: **Fixed.** Previous round replaced with `logger.warning()`.
> - Corrupt JSONL abort: **Partially addressed.** `_pull_reward_sessions_from_cloud` now catches per-file parse errors and continues (events default to `[]`). However, a corrupt line within a valid file still aborts that session's import.

Concurrency more broadly: the DB paths are mostly safe (FK cascade, unique `seq_no`, `with_for_update` on Postgres), but the JSON-file stores (`accounts.json` usage counters, filesystem session store, account_system) are all unlocked read-modify-write and unsafe under the background sync thread + concurrent requests.

---

## 10. Frontends

### 10.0 The three-frontend problem (High)

There are **three** UIs, and the deployed one is not the React app:

1. **Textual TUI** — `frontend/app.py` + `frontend/ui/*` + `frontend/services/*`. Launched locally by `scripts/dev/run_obital.ps1`/`scripts/dev/run_frontend_textual.ps1`.
2. **React/TypeScript app** — `frontend/src/**`. Built by `scripts/dev/build_frontend.ps1`, but **not actually served** (see §11).
3. **Vanilla-JS SPA** — `backend/app/templates/index.html` (≈150 KB, CDN Tailwind/marked/KaTeX). This is what FastAPI serves at `/` and therefore what Vercel ships. Evidence it is *not* the Vite output: zero `/assets/*.js` references and no single-file Vite plugin in `package.json`.

The committed comment at `templates/index.html:2-3` ("apply changes to BOTH files… `HTML_TUI_TEMPLATE`") is **stale** — that constant no longer exists in `main.py` — concrete evidence of divergence already biting.

**Recommendation (High):** Pick one web UI. Either commit to React (run `scripts/dev/build_frontend.ps1` in Vercel's build step, serve the real Vite output, delete `templates/index.html`) or commit to the vanilla template and delete `frontend/src`. Maintaining a React app that never ships is pure liability.

### 10.1 Textual TUI (`frontend/app.py`, `services/*`, `ui/*`)

- **High — authentication scraped from subprocess stdout.** `app.py:24,91-96` + `engine_cli_bridge.py:25-31`: login/PIN happen inside a spawned `engine` CLI, and the TUI learns the account id by regex-matching the CLI banner (`_SIGNED_IN_RE`). Any wording change silently breaks auth/account context. **Fix:** a structured login API called from `ApiClient`.
- **Medium — `account_id` defaults to `admin` and is sent as a query param** on every request (`api_client.py:12,45,57`) — the client-side face of §5.1.
- **Medium — 5-second forever-poll.** `app.py:122-125`: `_periodic_refresh` calls `list_lessons`+`list_docs` every 5s with no backoff/visibility check — constant cloud load/cost. **Fix:** longer interval, pause when unfocused, event-driven refresh after mutations (already partially present).
- **Medium — unawaited fire-and-forget tasks.** `app.py:96,117`: `asyncio.create_task(...)` handles aren't stored; exceptions are swallowed and tasks can be GC'd. **Fix:** Textual `run_worker`.
- **Low — SSE parsing handles only `data:` line prefixes** (`api_client.py:207-213`), tightly coupled to the backend's one-JSON-per-line format.

**Done well:** consistent `quote(..., safe='')` on path params, a single reused `httpx.AsyncClient` with timeout, clean `on_unmount` push+close, and snapshot-diffing to avoid needless pane re-renders.

### 10.2 React frontend (`frontend/src`)

Even though it is not currently deployed, its security issues matter if it ever ships:

- **Critical — XSS.** `lib/markdown.ts:26,59` returns `marked.parse(...)` HTML and a custom code renderer interpolates **unescaped** content (`<pre><code>${text}</code></pre>`), injected via `dangerouslySetInnerHTML` at `components/chat/MessageBubble.tsx:56`. LLM/synced content like `</code><img onerror=...>` executes. **Fix:** escape code text and run output through DOMPurify; or use the already-present `react-markdown` + `rehype-katex` and delete the hand-rolled file.
- **Critical — unlock token in `localStorage`.** `stores/authStore.ts:34,41,45,49`. Combined with the XSS, any injected script can exfiltrate the vault token. **Fix:** httpOnly cookie set by the backend, or at least `sessionStorage`, plus fixing the XSS.
- **Critical — token in SSE URL query string.** `api/client.ts:33-39` (`sseUrl()`): `?token=<unlockToken>` lands in logs, history, `Referer`. **Fix:** send as a header (the path used by `useChatStream` already does — `sseUrl` may be dead, remove it).
- **High — plaintext PIN/passphrase retained in global store** (`authStore.ts:6-7,38-39`, `LoginOverlay.tsx:81,103`). **Fix:** local component state, cleared immediately after unlock.
- **High — account-enumeration oracle** via empty-PIN login + error-string matching (`LoginOverlay.tsx:36-52`).
- **Medium — fetch client has no timeout/retry and unconditionally `res.json()`** (`api/client.ts:17,22`); a 204/HTML body throws an opaque parse error. **Fix:** `AbortController` timeout + content-type guard.
- **Medium — non-standard SSE reader** (`lib/sse.ts:33-47`) ignores event-block framing; **effect race/cleanup gaps** in `ChatView.tsx:20-27` (fetch on `[]`, errors swallowed, no `ignore` flag, no refetch on auth change); shared `abortRef` across sends (`useChatStream.ts:7,15-16,63`).
- **Low — type-safety leaks** (`unknown[]` response types, unchecked SSE casts) and **accessibility gaps** (icon-only buttons without `aria-label`, log panel not `aria-live`, overlays without verified focus-trap).

**Done well:** zustand stores read via `getState()` in callbacks (correct stale-closure avoidance), `AbortController` wired through streaming, immutable store updates, functional `appendStreamingToken`, centralized auth-header injection.

---

## 11. Deployment & operations

- **Critical — deploy serves the wrong/stale UI.** `.gitignore` ignores `frontend/dist/`; `vercel.json` only rewrites `/(.*)` → `api/index.py` with no Vite build step and no static serving. So Vercel ships whatever `backend/app/templates/index.html` is in git (the vanilla SPA), and the React build is never produced in CI. **Fix:** decide on one UI (see §10.0); if React, add a Vercel build and serve `dist/assets/*`.
- **High — catch-all rewrite to a Python file with no static handling.** `vercel.json:3` routes every request — including `/assets/*.js` if React were deployed — through FastAPI, which has **no** `StaticFiles` mount, so hashed assets would 404. **Fix:** serve static assets via Vercel static build/`outputDirectory`, or mount `StaticFiles` and exclude `/assets` from the rewrite.
- **Medium — serverless runtime under-specified.** `api/index.py:1` correctly exposes `app`, but `vercel.json` pins no Python `runtime`/`maxDuration`/`memory`. The app's `lifespan` runs migrations locally but **not** on Vercel (schema assumed pre-migrated), and SSE chat streams may exceed serverless function timeouts. **Fix:** pin runtime + `maxDuration`, document out-of-band migrations, verify streaming within limits.
- **Medium — `scripts/dev/run_obital.ps1` force-kills whatever owns port 8000** (not just processes it started). **Fix:** track the PID it launches.
- **Low — hand-rolled, duplicated `.env` parsing** in `scripts/dev/run_obital.ps1` and `scripts/dev/run_backend_cloud.ps1` (doesn't handle quoted values / `=` in values). Extract a shared helper.
- **Low — `scripts/dev/build_frontend.ps1` overwrites the committed template**, which is the mechanism by which the three-frontend divergence happens. Output to a dedicated dir, never to a hand-maintained template.

**Done well:** `scripts/dev/run_obital.ps1` polls `/health` before launching the TUI and stops the backend in `finally`; `scripts/dev/run_backend.ps1` clears `DATABASE_URL` to force local mode (clean local/cloud separation); migrations are gated on `DATABASE_URL`; `.gitattributes` enforces LF with CRLF carve-outs for scripts.

---

## 12. Testing

The Python test suites are **genuinely good** — real crypto round-trips, real assertions, deterministic, no live network/LLM. The problems are around coverage gaps and what's advertised.

- **High — README points at the wrong test command.** `README.md:184-190` tells users to run `python test_chat_api.py`, which is a **manual smoke script** requiring a running backend and a **real LLM** (sends "What is 2+2?"), non-deterministic, with its own pass/fail counter. The real pytest suite (18 backend files) is documented only in `backend/tests/README.md`. **Fix:** point README at `python -m pytest backend/tests/ …`; treat the root script as a dev utility.
- **Medium — stray root `test_chat_api.py`.** Confirmed **not** a duplicate of `backend/tests/test_chat_api.py` (root = network/LLM smoke; backend = 5 properly-mocked `unittest` cases). It pollutes the root and confuses pytest discovery. **Fix:** move to `scripts/` or delete.
- **Medium — the most security-relevant property is untested.** Tests thoroughly cover the unlock-token machinery, crypto envelope, brute-force lockout, and the full sync round-trip — but **nothing tests API-level tenant isolation** (whether `?account_id=X` without a token can read account X's data). Given §5.1, that is the gap that matters most. **Fix:** add authorization tests.

> **Status (2026-06-12):** Fixed. `backend/tests/test_auth_isolation.py` added with 10 test cases exercising the real FastAPI dependency chain: no-token rejection (3 routes), cross-account token rejection (alice→bob, bob→alice on usage and docs), expired token rejection, version-mismatch rejection, valid-token access, garbage-token rejection, and local single-user mode passthrough. Uses real `create_unlock_session`/`resolve_dek` with a fake DB backend.
- **Low — browser tests use hand-copied JS.** `test_browser_js.py` injects copies of grading JS extracted from `index.html`/`main.py`; `backend/tests/README.md` admits the copies must be manually kept in sync, so they can pass against stale logic. **Fix:** extract the JS to a real module imported by both app and tests.
- **Nit — duplicate import** at `backend/tests/test_sync_api.py:14-15`.

**Done well:** `helpers.make_account_row` does a real Argon2 KEK derivation + DEK wrap; `test_account_sync.py` is a real SQLite + AES-GCM round-trip asserting ciphertext ≠ plaintext; unlock-session tests cover client binding, version invalidation, lockout escalation, and counter reset; everything is hermetic and deterministic; there's even a named regression test for a past stale-blob bug.

---

## 13. CI/CD

- **High — CI hardens dependencies but proves nothing about the code.** `.github/workflows/engine-security-ci.yml` has two jobs that only pin pip tooling, do `--require-hashes` installs, and run `pip-audit`. There is **no** `pytest`, no lint (`ruff`/`flake8`), no type-check (`mypy`/`pyright`), and no frontend build (`npm run build`/`tsc`). The good tests never run automatically, and the `--no-deps` installs can't even catch the import breakage in §14. **Fix:** add a pytest job (backend + engine + frontend tests), a lint/type job, and an `npm ci && npm run build` job.
- **Medium — test deps never installed.** The app-audit job never installs `requirements-test.in` or the actual runtime deps, so import errors don't surface.
- **Low — PR template checklist is unenforced.** `pull_request_template.md` is excellent (scope, API-change table, migration order/rollback, correct pytest commands) but its "Lint/type/test checks pass" item isn't backed by CI.

**Done well:** hash-verified installs (`--require-hashes`), pinned pip/setuptools/wheel/pip-tools, `pip-audit` on every pinned set, and `paths:` filters. The supply-chain hardening is above average — it just needs the functional checks alongside it.

---

## 14. Dependency management

Eight+ requirements files across root/backend/frontend/engine, with two pinning philosophies.

- **High — the backend lock is not self-contained.** `backend/requirements.lock.txt` contains only fastapi/uvicorn/sqlalchemy/psycopg/alembic/cryptography/argon2. It is **missing** `openai`, `sentence-transformers`, `torch`, `pypdf`, `python-dotenv` — which `backend/app/services/chat_engine.py` (via `QuizIntentRouter`) and `embeddings.py` (`from sentence_transformers import …`) actually import. A clean `--require-hashes` install from the backend lock **cannot run chat or embeddings**; it implicitly relies on the foundation env. CI's `--no-deps` install hides this. **Fix:** make the backend lock self-contained, or document the install order.

> **Status (2026-06-12):** Partially addressed. `python-dotenv>=1.0.0` added to `backend/requirements.lock.in` and `backend/requirements.txt` (directly imported by `backend/app/main.py`). `openai` and `pypdf` are foundation-only deps (not directly imported by backend). `sentence-transformers` and `torch` were already present in the lock file. Remaining: the backend lock still relies on the foundation env for `openai`/`pypdf` transitive deps used via `engine` pipeline imports.
- **Medium — version-drift risk from duplication.** Root `requirements.txt` adds `openai`/`pydantic`/`pypdf`/`python-dotenv` that `backend/requirements.txt` omits; three hand-maintained copies of overlapping pins will drift (they agree today). **Fix:** generate the deployable install from the same `.in` as the backend lock.
- **Medium — mixed pinning strategy.** `engine/requirements.in` uses loose lower bounds (`openai>=1.40.0`, `sentence-transformers>=3.0.0`) while backend/root use exact pins; a fresh compile could pull an incompatible major (e.g. openai 1.x vs the app's 2.38). **Fix:** pin to compatible majors.
- **Medium — `torch` is explicitly pinned for compatibility, not as a complete security fix.** `engine/requirements.in` now constrains `torch==2.12.0`, the latest version available on the configured package index, so the transformer stack does not float unexpectedly. `pip-audit` still reports a PyTorch advisory for this version, so this should be treated as a temporary compatibility pin until a non-vulnerable supported PyTorch release is available or the local embedding dependency is made optional.

**Done well:** the lock files are `pip-compile --generate-hashes` outputs (SHA-256 hash-pinned); `package-lock.json` is committed for reproducible JS installs.

---

## 15. Documentation

Docs are extensive and mostly accurate, with one significant mismatch.

- **High — `SECURITY.md` threat model does not match the code.** It describes a hardened, token-bound model and strong guarantees but never discloses that `account_id` is unauthenticated/spoofable, that read endpoints serve data without a token, or that SQLite-mode data is plaintext at rest and readable by anyone who can reach the API (§5.1). **Fix:** add an explicit "Access control" subsection stating these facts, and either document them as accepted (local single-user) or fix the model.
- **Medium — README "Testing" documents the wrong command** (§12).
- **Low — README top-level framing is stale.** `README.md:1` still framed the repo as an offline pipeline ("the pipeline remains the source of truth"), but it is now a full FastAPI + web + sync product.
- **Low — verify the `deepseek-v4-flash` model id** referenced in README and `config.py`.

**Done well:** `backend/README.md` is candid and accurate — it explicitly documents the spoofable-`account_id` and optional-token behavior (the contradiction is specifically that `backend/README.md` is honest while `SECURITY.md` is not), with complete endpoint/error/env tables that match the code. `CHANGELOG.md` is detailed and dated; `ARCHITECTURE.md` and the quiz/pipeline docs are referenced consistently.

---

## 16. Repository hygiene

- **Critical — real user chat content committed.** `backend/data/chat/sessions.json` (≈68 KB, 807 lines) contains full tutor conversations, quiz Q&A, and study stats. Privacy/PII in git history. `.gitignore:24` lists `backend/data/` but the file predates that rule. **Fix:** `git rm --cached`, scrub from history (git filter-repo / BFG), confirm the ignore.
- **High — ~87 MB model committed.** `data/models/.../model.safetensors` (~90 MB). `.gitignore:27` lists `data/models/` but it's already tracked; **no Git LFS** is configured. Bloats every clone. The code already lazy-loads the model and has a hash fallback. **Fix:** `git rm -r --cached data/models/`, scrub from history, fetch at runtime (do **not** add to LFS).
- **High — `.idea/` committed** (7 files), not in `.gitignore` at all. **Fix:** add `.idea/` to `.gitignore`, `git rm -r --cached .idea`.
- **Medium — committed build artifacts:** `backend/app/templates/index.html` (generated UI, §10.0/§11) and `frontend/tsconfig.tsbuildinfo` (TS incremental cache). **Fix:** generate at build time; add `*.tsbuildinfo` to `.gitignore`.
- **Medium — `.gitignore` typo:** line 34 ignores `snitch_assets/` but the directory is `stitch_assets/`, so the design-mockup PNGs/HTML are tracked. **Fix:** correct the typo (or remove the assets).
- **Nit — `.gitattributes`** declares `*.gguf binary` but the tracked model is `.safetensors`; add `*.safetensors binary` if any remain (ideally none).

**Done well:** `.gitignore` otherwise correctly keeps `*.db`, `accounts.json`, `__pycache__`, and `node_modules` untracked; `.gitattributes` enforces consistent EOL/binary handling.

---

## 17. What is done well

It is worth stating clearly: the core of this project is solid, and several parts are genuinely above the level expected for a project of this scale.

1. **Cryptographic envelope.** Argon2id KEK derivation with serializable params, per-account DEK wrapping, AES-GCM with per-message random nonces, a versioned `enc:v1:` prefix for migration, and SHA-256 integrity verification on decrypt. Clean, correct primitives.
2. **Unlock-session design.** Token stored only as a SHA-256 hash, DEK Fernet-encrypted at rest, TTL, client binding, fail-closed decryption, and brute-force lockout with exponential backoff on the passphrase path.
3. **Thread-safe lazy singletons** for the embedding model and quiz router (double-checked locking, failure latch, sensible fallback) — correct for the warm-up-in-a-thread startup.
4. **Pragmatic cross-backend message ordering** (`with_for_update` on Postgres, SQLite fallback, unique `seq_no` + bounded retry).
5. **Careful streaming hygiene** — the DSML/tool-markup stripper buffers a marker-width holdback window so split tags can't leak to the client, and guarantees a terminal `done` event.
6. **`HybridContextBuilder`** — fingerprinted stable prefix + canonicalized JSON for prompt-cache efficiency and reproducibility.
7. **Real, hermetic tests** for the crypto, unlock, and sync layers, including a named regression test for a past bug.
8. **Supply-chain hardening in CI** — hash-verified installs and `pip-audit` on every pinned dependency set.
9. **Honest low-level docs** — `backend/README.md` accurately describes the actual auth behavior, and the PR template is thorough.
10. **Graceful degradation** — embedding hash-fallback, LLM "local mode" fallback, and read-only-filesystem detection for container/cloud hosts.

---

## 18. Prioritized remediation roadmap

### P0 — before any non-local / cloud deployment

1. **Authorization:** add a `require_account()` dependency (valid token → token-bound `account_id`) and apply it to every account-scoped route; make `account_id` non-defaulting; gate any unauthenticated single-user behavior behind an explicit flag. (§5.1)
2. **Lock down LLM endpoints:** require token, bound the account, rate-limit, hard-cap body sizes, run generation as a background job, return generic errors. (§5.2, §7)
3. **Repo hygiene:** `git rm --cached` the model, chat logs, `.idea/`, and build artifacts; scrub history; fix the `.gitignore` typo. (§16)
4. **CORS:** pin `allow_origins`. (§5.4)
5. **If the React app is to ship, fix its three Criticals** (XSS, token storage, token-in-URL); otherwise delete it. (§10.2, §10.0)

### P1 — correctness & reliability

6. Make E2EE fail-closed everywhere a DEK is required. (§5.3)
7. ~~Redesign sync to be incremental with conflict resolution and a spanning transaction. (§9)~~ **Done (2026-06-12).** Pull-before-push ordering, `updated_at`-based conflict resolution, diff-based child insert, and merge helpers all implemented.
8. Move blocking LLM/model/`asyncio.run` work off request paths into threadpools/background jobs; fix the tool-call truncation 400. (§6, §7)
9. Fix the `usage.py` naive-datetime crash and the unlocked usage-counter race. (§6)
10. Add a pytest + lint + type + frontend-build job to CI; make the backend lock self-contained. (§13, §14)
11. Add API-level tenant-isolation tests. (§12)
12. Add PIN throttling/lockout and force-migrate legacy PIN hashes. (§5.5)
13. Reconcile `SECURITY.md` with the real access-control model. (§15)

### P2 — performance, maintainability, polish

14. Centralize a single cached embedding-model loader with consistent offline settings; include model/chunk params in the cache key. (§8)
15. Cache document extraction/embeddings; fix the `get_doc_texts_for_chunk_ids` N+1. (§6)
16. Decide on one web frontend and delete the other; output builds to a dedicated dir. (§10.0, §11)
17. Add retry/backoff/timeout to pipeline LLM calls; improve chunking to respect boundaries. (§8)
18. Consolidate the dependency files and pinning strategy; fix README framing and test command. (§14, §15)
19. Clean up dead code, mid-file imports, `print`→logger, joke comments, and inconsistent status codes. (§6, §7)

---

## 19. Appendix: finding index

Selected findings with primary locations (severity in brackets).

**Security / authz**
- [Critical] Spoofable `account_id`, optional token — `backend/app/api/common.py:17-36`
- [Critical] Unauthenticated LLM endpoints — `backend/app/api/pipeline.py:131-195`; `backend/app/api/quiz.py:87-106`
- [High] Plaintext-on-missing-DEK — `chat_tools.py:589-617`; `storage.py:727,804`; `account_sync.py:287,439,477`
- [High] CORS wildcard + credentials — `backend/app/main.py:47-53`
- [High] PIN: no lockout / legacy unsalted hash — `account_system.py:67-85,674-701`; `auth.py:175-183`
- [High/Med] Error leakage — `pipeline.py:158,195,240`; `chat.py:71`; `auth.py:171-172`
- [Med] Prompt injection — `qa_pipeline.py:247-267`

**Backend services / API**
- [High] `asyncio.run` on request path — `chat_tools.py:619-657`
- [High] Tool-call truncation → 400 — `chat_engine.py:434-464,676-708`
- [High] Usage counter race / naive-datetime crash — `usage.py:24,53-67`
- [High] N+1 doc loads — `storage.py:594-625`
- [High] Unbounded upload into memory — `doc.py:114-118`
- [High] Two account-id validators — `common.py:11` vs `account_paths.py:7`
- [Med] Re-extraction per chat message — `storage.py:262-301`
- [Med] Settings decrypt → ciphertext as prompt — `settings_service.py:166-172`
- [Med] Hardcoded embedding dim — `embeddings.py:18-19,46`
- [Low] `quiz_service` ignores `num_questions` — `quiz_service.py:35`

**Pipeline / quiz**
- [High] Model loaded 4× / `rag_engine` ignores offline — `rag_engine.py:169-186`
- [Med] Incomplete cache key / char-offset chunking — `rag_engine.py:92-104,254-265`
- [Med] No retry/backoff/timeout on LLM calls — `qa_pipeline.py:168-173,278-283`
- [Med] Full-store rewrite, no lock/atomicity — `account_system.py:502-557`
- [Med] Batch ingest aborts on one bad file — `extract_text.py:18,28-33`; `main.py:401-423`

**Sync / concurrency**
- ~~[High] Destructive non-incremental sync — `sync_scheduler.py:63-103`; `account_sync.py:266-299`~~ **Resolved (2026-06-12).**

**Frontends / deploy**
- [High] Three frontends / divergence — `templates/index.html:2-3`; `frontend/src/**`
- [High] TUI auth scraped from stdout — `app.py:24,91-96`
- [Critical] React XSS / token storage / token-in-URL — `lib/markdown.ts:26,59`; `MessageBubble.tsx:56`; `authStore.ts:34`; `api/client.ts:33-39`
- [Critical] Deploy serves wrong UI / catch-all rewrite — `vercel.json:3`; `scripts/dev/build_frontend.ps1`

**Tests / CI / deps / docs / hygiene**
- [High] CI runs no tests/lint/types/build — `.github/workflows/engine-security-ci.yml`
- [High] Backend lock missing runtime deps — `backend/requirements.lock.txt`
- [High] README wrong test command — `README.md:184-190`
- [High] SECURITY.md mismatches auth model — `SECURITY.md:82-97`
- [Critical] Committed chat logs — `backend/data/chat/sessions.json`
- [High] Committed ~87 MB model — `data/models/.../model.safetensors`
- [High] Committed `.idea/`; [Med] `.gitignore` `snitch`→`stitch` typo — `.gitignore:34`

---

*End of review. No source files were modified in producing this document.*
