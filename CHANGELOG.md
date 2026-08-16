# Changelog

## 2026-08-10

### Repository cleanup, `main` promotion, and Orbital submission prep

**`step12_foundation/` renamed to `engine/`.** The package was named after an
assignment milestone rather than its contents: the document pipeline, RAG
index, quiz engine, and shared account storage. Renamed across 131 files with
a single identifier-level replacement covering all five token forms in the
tree (dotted module path, forward- and back-slash paths, quoted path segments,
and the hyphenated workflow filename). Entries below this one keep the old
name, since they describe the code as it was.

- `frontend/services/foundation_cli_bridge.py` → `engine_cli_bridge.py`, and
  `FoundationCliBridge` → `EngineCliBridge`, so the subprocess bridge no longer
  carries the old package name. ([frontend/services/engine_cli_bridge.py](frontend/services/engine_cli_bridge.py))
- `.github/workflows/step12-foundation-security-ci.yml` → `engine-security-ci.yml`,
  including its two self-referencing path filters.
- `.gitignore` retargeted to `engine/{cache,output,accounts}/`. Missing this
  would have made `engine/accounts/` — PIN hashes, wrapped DEKs and user
  documents — tracked, with no failure signal.
- Removed the 13 flat re-export shim modules (`engine/main.py`,
  `engine/config.py`, …). Nothing imported the flat form; the documented
  `python -m` entrypoints now address modules at their real location.
- The pipeline's own "Step 1/2/3" stage numbering is unchanged: it documents
  pipeline stages, not the package.

Local developers must move their ignored data after pulling:

```bash
mv step12_foundation/accounts engine/accounts   # and cache/, output/
git check-ignore -v engine/accounts engine/cache engine/output
```

Any bin/restore rows created before the rename store absolute paths containing
the old directory name:

```sql
UPDATE bin_items SET metadata_json = replace(metadata_json, 'step12_foundation', 'engine');
```

### Continuous integration — red for 17 consecutive runs, now green

Four of the five workflows only triggered on `main`/`dev`, so they had not run
on the working branch at all and several failures had accumulated unseen.

- **Stale mock assertion.** `test_material_detail_returns_body_only_for_owned_lesson`
  expected `get_lesson_progress(username=...)` but the call site passes
  `user_dek` for E2EE unwrapping. ([backend/tests/test_lesson_materials.py](backend/tests/test_lesson_materials.py))
- **Broken lockfile.** `npm ci` failed on missing `@emnapi/core` and
  `@emnapi/runtime` and a `@emnapi/wasi-threads` mismatch. Regenerated with
  `--package-lock-only`; `package.json` and `lockfileVersion` unchanged.
- **Missing licence notices.** Five direct npm dependencies were absent from
  `THIRD_PARTY_NOTICES.md`.
- **Unsatisfiable lockfile hashes.** `engine/requirements-security.txt`
  recorded digests for `python-multipart==0.0.31` and `starlette==1.3.1` that
  match no artifact PyPI has ever published. A June commit bumped the version
  lines and left the `--hash` lines untouched, so `--require-hashes` had been
  impossible to satisfy since mid-June. Replaced with the published digests.
- **14 dependency advisories.** `pypdf` 6.12.2 → 6.15.0 (10 advisories),
  `cryptography` 48.0.1 → 50.0.0 (3), `setuptools` 81.0.0 → 83.0.0 (1).
  Applied surgically rather than by re-running `pip-compile`, so no unrelated
  pin moved. `cryptography` was already resolving to 50.x in production via the
  root `>=48.0.1` range; this aligns the lockfile with what actually deploys.
- **`pip-audit` flag error.** The root audit used `--disable-pip` against an
  unhashed requirements file. Dropped the flag so pip-audit resolves the ranges
  itself, which also audits the transitive tree the deployment receives.
- **npm audit gate.** `npm audit fix` cleared six advisories including the
  critical node-tar set. The remaining `sharp` advisory has no published fix,
  so added [scripts/check_npm_audit.py](scripts/check_npm_audit.py) — an
  allowlist gate mirroring the `--ignore-vuln` pattern the Python audits
  already use. See SECURITY.md for why sharp is not reachable in the shipped
  bundle.
- Branch filters now target `main` and `test-good-try`; `dev` and
  `Ian-Cleanup` never existed. Removed the `paths` filter from License CI's
  `push` trigger, which is why the notices file drifted unnoticed. Added
  `permissions: contents: read` to the three workflows lacking it.

### Repository hygiene

- **`backend/requirements.txt` was unusable on its own** — it omitted `openai`,
  `pypdf` and `pydantic`, all imported directly, so a backend-only install
  produced an app that 500s on the first chat message.
- Added [pyproject.toml](pyproject.toml) with `pythonpath = ["."]`. The suite
  imports `backend.app` and `engine` as top-level packages, which previously
  resolved only because CI invokes `python -m pytest`; a bare `pytest` failed.
- Added [.python-version](.python-version) (3.13) and moved Core Quality CI
  from 3.12, so all five workflows and local development agree.
- Added [.vercelignore](.vercelignore). Note the deliberate absence of a
  blanket `*.md` rule: `backend/prompts/*.md` are read at runtime.
- **`scripts/dev/build_frontend.ps1` never cleaned `assets/`** — `Get-ChildItem`
  lacked `-Recurse`, so `Copy-Item` merged each build into the previous one.
  That is why ~30 builds left 60 orphaned bundles (~45 MB). Fixed; existing
  committed assets were left in place.
- Replaced the last user-visible "StudyBuddy" strings in live code: the lesson
  transcript speaker label, the quiz CLI banner, and the tutor system prompt.
- `docs/product/STUDYBUDDY_README.md` → `PRODUCT_GUIDE.md`, rebranded to
  Obital / Team Obital. `docs/archive/studybuddy-backend-prd.md` →
  `legacy-backend-prd.md`, contents unchanged. Deleted
  `docs/StudyBuddy-Obital-README.pdf` (2.1 MB, no inbound links, a stale print
  export of the markdown).
- Dependabot was restored and then disabled again: it immediately opened 23
  update branches, which is the opposite of the clean two-branch repository
  this submission needs. Restore after submission with
  `git show 559649b:.github/dependabot.yml > .github/dependabot.yml`.

### Documentation accuracy

- Corrected the maintained test counts everywhere they appear. The README also
  omitted `test_lesson_materials.py` from the API/integration command, which is
  why it reported 31 against CI's 40. Verified counts are now 44 unit,
  40 API/integration, 40 browser runtime.
- [.env.example](.env.example) documented 20 of the 44 environment variables
  the code reads. All are now listed with their real defaults, including the
  credential-bearing `ACCOUNT_SYNC_PASSPHRASE`.
- Rewrote the project layout tree in
  [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md): it
  placed `backfill_tags.py` under `backend/` when it lives in
  `backend/scripts/`, and omitted `frontend/src/` — the React app that is
  actually deployed.
- The 8 `file:///c:/Users/n8469/...` links in this file pointed at a specific
  machine and two named paths that no longer exist. Rewritten as repo-relative
  links, which also removes a username from the history.
- Documented the repository tooling files and the CI audit scripts in the
  README and `scripts/README.md`.

### Branches

`main` now carries the cleaned trunk, with a merge whose tree is byte-identical
to `test-good-try` while keeping main's three earlier commits in the ancestry.
Six stale branches were deleted; the two carrying unique commits are preserved
as `archive/workspace` and `archive/sidebar-collapse` tags.

## 2026-06-15

### Lesson Harness — LLM-Generated Lessons with Embedded Quizzes

- **New lesson generation engine** (`services/lesson_harness.py`, 1668 lines): Full async pipeline (`_generate_lesson`) that takes a topic, collects context from user documents or Wikipedia, runs a planner LLM to produce a 1–5 part curriculum plan, generates each part in parallel via worker LLM calls (with web search tools), reviews quality with a reviewer LLM, and saves to AccountStore via `start_lesson()`. (`backend/app/services/lesson_harness.py`)
- **Background job system:** `create_lesson_generation_job()` queues a job in `JsonJobStore` and spawns a daemon thread. Job state tracked with subtasks (planner, workers, reviewer, saver) and SSE-friendly status/phase/progress updates. (`backend/app/services/lesson_harness.py`)
- **Quiz normalization:** `_normalize_quiz()` converts raw LLM quiz output to structured `QuizQuestion`-compatible dicts with Pydantic validation. Falls back to single goal-based question if none valid. (`backend/app/services/lesson_harness.py`)
- **Quiz grading endpoints:** `POST /lessons/{id}/checkpoints/{index}/grade` grades checkpoint answers against embedded quiz data via `grade_lesson_quiz()`. Returns `GradeCheckpointResponse` with `passed`, `score_percent`, per-question results. (`backend/app/api/lesson.py`)
- **Lesson generation API:** `POST /lessons/generate` triggers generation, `GET /lessons/jobs/{id}` polls status, `GET /lessons/jobs` lists recent jobs, `POST /lessons/jobs/{id}/cancel` cancels in-flight generation. (`backend/app/api/lesson.py`)
- **`grade_lesson_quiz()`** reconstructs `QuizQuestion` from embedded checkpoint quiz data, builds `QuizBuildResponse`, calls `quiz_backend.grade_quiz()`. MCQ: exact match; open: semantic 70% + lexical 30%, threshold 0.58. (`backend/app/services/quiz_service.py`)
- **`build_checkpoint_quiz()` disabled** — raises `RuntimeError` instead of building from QA pool. Lessons now require harness-generated embedded quizzes. (`backend/app/services/quiz_service.py`)
- **All lesson creation routes through harness:** `create_lesson_payload(use_harness=True)` is the central entry point. Returns `harness_job_id` for background tracking. (`backend/app/services/chat_tools.py`)
- **Quiz tooling:** `submit_answer_payload()` grades each question, advances state, and on last question auto-submits, applies lesson attempt, and advances checkpoint if passed. (`backend/app/services/chat_tools.py`)
- **Background QA generation:** `_try_generate_qa()` runs `CurriculumQAPipeline` asynchronously in a background thread, caches results per account. (`backend/app/services/chat_tools.py`)

### Agent Tool Harness — Multi-Round Tool Calling

- **DSML tool call parsing:** `parse_dsml_tool_calls()` and `strip_dsml()` extract tool invocations from model-generated DSML markup, supporting both streaming and non-streaming paths. (`backend/app/services/agent_harness.py`)
- **Multi-round agent loop:** `run_agent_turn()` runs up to 3–5 rounds of tool calls per user message with safety levels (`read`/`mutation`). Mutation tools require user confirmation via `put_pending_action()`. (`backend/app/services/agent_harness.py`)
- **Search budget tracking:** `SearchBudget` limits web searches to 3 provider requests per message. (`backend/app/services/agent_harness.py`)
- **Pending action store** (`services/harness_state.py`): `put_pending_action()` / `pop_pending_action()` stores pending mutation tool actions in `harness_pending.json` with session/account validation. (`backend/app/services/harness_state.py`)
- **Tool confirmation endpoint:** `POST /chat/sessions/{id}/confirm` approves or cancels pending agent tool actions. (`backend/app/api/chat.py`, `backend/app/services/chat_service.py`)

### Study Tools — Agent Tool Registry

- **New `create_lesson` tool:** Routes to `chat_tools.create_lesson_payload()` with `use_harness=True`. (`backend/app/services/study_tools.py`)
- **New `search_web` and `search_wikipedia` tools:** Web search for agent use, backed by Exa and Wikipedia APIs. (`backend/app/services/study_tools.py`)
- **New `save_generated_qa` and `start_qa_generation_job` tools:** Pipeline artifact management — writes encrypted QA to DB/filesystem, spawns background QA generation. (`backend/app/services/study_tools.py`)
- **Conditional quiz tools:** `submit_answer`, `review_quiz`, `exit_quiz`, `submit_quiz` only added to tool spec when an active quiz exists. (`backend/app/services/study_tools.py`)

### LLM Infrastructure

- **`call_json_model()`** — generic async OpenAI-compatible JSON model caller with streaming support for DeepSeek thinking/reasoning mode. Includes `event_callback` for model start/delta/done events. (`backend/app/services/llm_json.py`)
- **`call_json_model_with_tools()`** — extended version supporting OpenAI tool/function calling (used by lesson workers for web search). (`backend/app/services/llm_json.py`)
- **Background job store** (`services/background_jobs.py`): `JsonJobStore` — thread-safe JSON-backed job storage with create/get/set/subtask operations and cancellation support. (`backend/app/services/background_jobs.py`)
- **QA generation job runner** (`services/generation_jobs.py`): `create_qa_generation_job()` spawns a daemon thread for background QA generation; `get_generation_job()` polls status from `generation_jobs.json`. (`backend/app/services/generation_jobs.py`)

### Web Search

- **Exa + Wikipedia hybrid search:** `search_web_and_wikipedia()` runs both in parallel via `ThreadPoolExecutor`, merges and deduplicates up to 5 results. Wikipedia uses `srwhat=text` snippet extraction with cleaned HTML. (`backend/app/services/web_search.py`)

### Chat Engine — Streaming & Tool Integration

- **SSE streaming endpoint:** `POST /chat/sessions/{id}/messages/stream` yields `token`, `tool`, `done`, and lesson job status events (`plan_step`, `subagent_start`, etc.) with SSE formatting. (`backend/app/api/chat.py`, `backend/app/services/chat_service.py`)
- **Streaming lesson job polling:** `send_chat_message_stream()` detects `create_lesson` tool output with `lesson_job_*` ID and yields real-time job status events. Configurable timeout (`OBITAL_LESSON_JOB_STREAM_TIMEOUT_SEC`, default 120s). (`backend/app/services/chat_service.py`)
- **`create_lesson` and `search_wikipedia` tools** added to `_BASE_TOOLS` in the AI tutor engine. `generate_reply()`/`generate_reply_stream()` use `run_agent_turn()` for multi-round tool calling with DSML stripping and DeepSeek thinking mode support. (`backend/app/services/chat_engine.py`)
- **Chat session listing:** `GET /chat/sessions` returns last 20 sessions with titles. (`backend/app/api/chat.py`)
- **SSE unlock error handling:** Returns user-friendly "Unlock required" message when `user_dek` is missing. (`backend/app/api/chat.py`)

### Pipeline API

- **Background QA generation jobs:** `POST /pipeline/jobs/qa-generation` creates job, `GET /pipeline/jobs/{id}` polls status via `generation_jobs.py`. (`backend/app/api/pipeline.py`)

### Auth & Sync

- **`POST /auth/bootstrap`** — single post-login endpoint performing unlock + sync pull (essentials mode) + hydration status. Sync pull uses `content_mode="essentials"` with background hydration. (`backend/app/api/auth.py`)
- **Lock-time sync push:** `POST /auth/lock` now pushes local data to cloud (SQLite mode) before clearing unlock session. (`backend/app/api/auth.py`)
- **Client validation:** `_validate_client_id()` requires `X-Client-Id` header (min 8 chars) on unlock and bootstrap. (`backend/app/api/auth.py`)
- **`GET /sync/status`** — returns hydration status from `get_hydration_status()`. (`backend/app/api/sync.py`)

### Schemas

- **`LessonCheckpoint` updated:** Added `markdown`, `quiz`, `sources` fields for harness-generated content. (`backend/app/schemas.py`)
- **New schemas:** `ConfirmActionRequest`/`ConfirmActionResponse` (tool confirmation), `AccountUsage`, `RewardTrendPoint`, `RewardSummary`, `RewardsDashboardResponse`, `BinItem`, `ChatMessage.lesson_id`/`doc_chunk_ids`. (`backend/app/schemas.py`)

### Logging

- **Structured logging:** `OBITAL_LOG_LEVEL` (default `INFO`) and `OBITAL_LOG_FILE` (default `backend/data/logs/backend.log`) env vars. Console and file handlers configured at startup with formatted timestamps. (`backend/app/main.py`)

### Hotfixes

- **Async auth dependencies:** `require_account` and `optional_user_dek` in `common.py` converted to `async def` to support async database lookups. (`backend/app/api/common.py`)
- **Doc count fix:** `GET /usage` now only counts documents with `upload_` prefix (excludes system artifacts). (`backend/app/api/usage.py`)
- **User DEK propagation:** `_run_lesson_generation_job()` sets `active_user_dek` context for background job threads. (`backend/app/services/lesson_harness.py`)

## 2026-06-12

### Bin / Trash System

- **Soft-delete with bin:** Deleting a document or lesson now moves it to the `bin_items` table with a 30-day TTL instead of immediate removal. Items can be restored or permanently deleted. (`backend/app/services/storage.py`, `backend/app/api/bin.py`)
- **Bin API:** Added `GET /bin` (list), `POST /bin/{bin_id}/restore` (restore), and `DELETE /bin/{bin_id}` (permanent delete) endpoints under the `/bin` prefix. (`backend/app/api/bin.py`)
- **Bin data model:** Added `BinItemModel` SQLAlchemy model (`bin_items` table) with columns: id, account_id, item_type, item_id, name, metadata_json, deleted_at, expires_at. (`backend/app/services/models.py`, `backend/alembic/versions/20260612_02_bin_items.py`)
- **Bin Pydantic schema:** Added `BinItem` schema with id, item_type, item_id, name, deleted_at, expires_at, days_remaining. (`backend/app/schemas.py`)
- **Restore logic:** Document restore clears `deleted_at`/`expires_at` on artifact rows; lesson restore re-injects saved state via `AccountStore.restore_lesson()`. (`backend/app/services/storage.py`, `backend/app/api/bin.py`)
- **Permanent delete:** Removes bin row and associated artifact rows (DB mode) or files (file mode). (`backend/app/services/storage.py`)
- **Background auto-cleanup:** Added `bin_cleanup.py` — daemon thread that calls `permanently_delete_expired()` every 3600s (configurable via `BIN_CLEANUP_INTERVAL_SECONDS`). Started/stopped via FastAPI lifespan. (`backend/app/services/bin_cleanup.py`)
- **Frontend bin view:** Added `BinFullScreen.tsx` React component with restore/delete buttons, accessible via right-panel Bin tab. (`frontend/src/components/fullScreen/BinFullScreen.tsx`)
- **Frontend API layer:** Added `listBin()`, `restoreBinItem()`, `permanentDeleteBinItem()` to TypeScript API client; added `list_bin()`, `restore_bin_item()`, `permanent_delete_bin_item()` to Python API client. (`frontend/src/api/bin.ts`, `frontend/services/api_client.py`)
- **Textual TUI commands:** Added `/bin` (list) and `/bin restore <id>` handlers. (`frontend/app.py`)
- **Toast notifications:** Document deletion shows "Document moved to bin", lesson deletion shows "Lesson moved to bin". (`frontend/src/components/fullScreen/DocFullScreen.tsx`, `frontend/src/components/fullScreen/LessonFullScreen.tsx`)

### Deferred Artifact Hydration

- **`hydration_status` column:** Added `hydration_status` (`VARCHAR(24)`, default `"full"`) to `AccountArtifactModel`. Values: `"full"` (content present), `"metadata_only"` (sha256/size/timestamps known, content empty), `"failed"` (decrypt or hash-verify failed). (`backend/app/services/models.py`)
- **Runtime schema migration:** `_ensure_runtime_schema()` in `db.py` auto-adds `hydration_status` column to existing `account_artifacts` tables on startup — uses `inspect()` to check column existence before running `ALTER TABLE`, so existing databases upgrade without a manual Alembic step. Applied to both local and cloud engines. (`backend/app/services/db.py`)
- **Content-pull modes:** `_pull_artifacts_from_cloud_sqlite` accepts `content_mode: ContentPullMode` — `"full"` (download all) or `"essentials"` (only QA artifact gets content; doc artifacts get metadata-only placeholders). (`backend/app/services/account_sync.py`)
- **On-demand hydration:** Added `hydrate_account_artifacts(account_id, dek, *, limit, paths)` — downloads + decrypts deferred artifacts in a separate pass. Supports limiting count and filtering by path. Returns `{hydrated, failed, remaining}`. Added `hydrate_account_artifact()` single-artifact wrapper. (`backend/app/services/account_sync.py`)
- **Hydration status API:** Added `get_hydration_status(account_id)` — returns per-account counts of full/metadata_only/failed artifacts plus remaining bytes. (`backend/app/services/account_sync.py`)
- **Storage layer hydration awareness:** `_load_curriculum_qa`, `_read_stateless_docs`, `_attach_embeddings`, `get_doc` all check `hydration_status` — skip or trigger on-demand hydration before reading content. `get_doc` calls `hydrate_account_artifact()` inline when a metadata-only doc is requested with a valid DEK. (`backend/app/services/storage.py`)
- **Selective column loading on pull:** Artifact list queries from cloud use `load_only` to avoid fetching `content` BLOBs; content is fetched in a second batched query only for candidates that require download. Same optimization on local side for push. (`backend/app/services/account_sync.py`)

### Sync Performance Optimization

- **Skip encrypt/decrypt for unchanged artifacts:** `_push_artifacts_to_cloud_sqlite` and `_pull_artifacts_from_cloud_sqlite` compare metadata (sha256, size, updated_at, deleted_at, expires_at) before encrypting/decrypting content. If nothing changed, the expensive crypto call is skipped entirely. Added `_same_artifact_metadata()` helper. (`backend/app/services/account_sync.py`)
- **Selective column loading on push:** Artifact list queries use `load_only` for metadata columns; content is loaded in a separate second query only for upload/download candidates. (`backend/app/services/account_sync.py`)
- **Tests:** Added `SqliteArtifactMetadataSkipTests` — mock-verified tests that `encrypt_bytes`/`decrypt_bytes` are not called when metadata is unchanged. Added `SqliteBinSyncTests` for bin sync push/pull and stale-restore guard. `SqliteArtifactMetadataSkipTests.test_essentials_pull_defers_document_content_then_hydrates_later` verifies deferred hydration flow. (`backend/tests/test_account_sync.py`)

### Cloud Sync of Bin Items (SQLite Mode)

- **Bin sync push:** `_push_bin_items_to_cloud_sqlite` mirrors local `BinItemModel` rows to cloud DB — upserts by ID, deletes stale rows. Called from `push_account_to_cloud`. (`backend/app/services/account_sync.py`)
- **Bin sync pull:** `_pull_bin_items_from_cloud_sqlite` mirrors cloud bin rows to local, with `_cloud_bin_item_is_stale_for_local_restore` guard that skips rows for artifacts the user restored locally after the bin entry was created (compares artifact `updated_at` ≥ bin `deleted_at`). Called from `pull_account_from_cloud`. (`backend/app/services/account_sync.py`)
- **Timestamp correctness:** Push/pull faithfully propagates `deleted_at`, `expires_at`, and `updated_at` instead of always stamping `now`; timestamp comparison prevents overwriting newer data. `delete_doc` sets `updated_at = now` on binned artifacts; `restore_doc` sets `updated_at = datetime.now(UTC)` on restored artifacts. (`backend/app/services/account_sync.py`, `backend/app/services/storage.py`)
- **Frontend optimistic UI:** Restore/delete buttons now remove items immediately from the list instead of waiting for a refetch; `pendingId` state disables buttons during in-flight operations to prevent double-clicks. (`frontend/src/components/fullScreen/BinFullScreen.tsx`)`

### Code Review Fixes (§7 Backend API router layer)

- **Unified account-id validator (§7):** Removed redundant `_ACCOUNT_ID_RE` regex from `common.py` — `_validate_account_id_format` now delegates directly to `normalize_account_id()` from `account_paths.py`. Eliminates the conflict where `common.py` allowed uppercase and min 3 while `account_paths.py` required lowercase and min 2. Invalid IDs now return `422` consistently. (`backend/app/api/common.py`)
- **Consistent RuntimeError→HTTPException mapping (§7):** Added `handle_user_error(exc, not_found=False)` helper in `common.py` — converts service-layer `RuntimeError` into a consistent `HTTPException` (400 for business-rule violations, 404 for not-found). Applied to `lesson.py` routes. (`backend/app/api/common.py`, `backend/app/api/lesson.py`)
- **Fixed `restart_lesson` hardcoded index (§7):** `POST /lessons/{id}/restart` now returns the actual `current_checkpoint_index` from the account store instead of hardcoded `0`. (`backend/app/api/lesson.py`)
- **Typed Pydantic model for quiz grading (§7):** `GradeQuizRequest.answers` changed from `list[dict[str, str]]` to `list[QuizSubmissionAnswer]` — returns `422` with field-level validation errors instead of `500` on missing `question_id`/`answer`. (`backend/app/api/quiz.py`)
- **Upload size enforcement (§7):** Added `UploadSizeLimitMiddleware` to `main.py` — rejects `POST/PUT/PATCH` requests with `Content-Length > OBITAL_MAX_UPLOAD_MB` (default 20 MB) at the ASGI layer. Added defense-in-depth streaming chunked read with incremental size cap in `doc.py`. (`backend/app/main.py`, `backend/app/api/doc.py`)
- **Background embedding on upload (§7):** `POST /docs/upload` now schedules embedding generation via FastAPI `BackgroundTasks` instead of running `embed_texts` inline on the request. Uses actual chunk IDs from `doc.chunks`. (`backend/app/api/doc.py`)
- **Pagination on list endpoints (§7):** Added `limit` (1–100, default 20) and `offset` (≥0) query parameters to `GET /docs` and `GET /lessons`. (`backend/app/api/doc.py`, `backend/app/api/lesson.py`)
- **Test suite updated:** Added `dependency_overrides` for `require_account` in `test_api_validation.py`, `test_chat_api.py`, `test_rewards_api.py`, `test_settings_api.py`, `test_stream_and_routes.py` to work with mandatory auth in non-local mode. 154 tests pass.

## 2026-06-08

### RAG Search Moved to Chat Engine
- **RAG search now lives in `chat_engine.py`:** Moved semantic search (`embed_text` + `search_chunks_by_vector`) from `chat_service.py` into `generate_reply()` and `generate_reply_stream()`. The engine is now self-contained — any caller passing empty `doc_texts` gets automatic RAG search.
- **Wikipedia as LLM tool:** `search_wikipedia` is now a tool the LLM can call when it needs external knowledge not in the student's documents. Returns short snippets (not full articles). Rate-limited to 10 calls/60 seconds.
- **Fixed Wikipedia 403:** Added `User-Agent` header to Wikipedia API requests (required by MediaWiki API etiquette policy).
- **LRU embedding cache:** Added `_cached_embed_text()` — an LRU cache (128 entries) wrapping `embed_text()` to avoid re-embedding repeated queries.
- **Module-level constants:** Extracted `_RAG_TOP_K=3` and `_RAG_THRESHOLD=0.35` as module-level constants in `chat_engine.py`.
- **`chat_service.py` simplified:** Removed `_auto_search_chunks()` function. Both `send_chat_message` and `send_chat_message_stream` now only resolve explicit `doc_chunk_ids` references; auto-search is handled by the engine.

## 2026-06-06

### Lesson Quiz Integration
- **Lesson commands in Web TUI:** Added `/lessons start <topic>`, `/lessons restart <N>`, `/lessons continue`, `/lessons status`, `/lesson quiz`, `/lesson answer <A,B,C,...>`, and `/lesson status` to the browser terminal. Lessons and quizzes are now fully connected — selecting a lesson and running checkpoint quizzes records attempts and advances progress.
- **Lesson commands in Textual TUI:** Added `/lesson start`, `/lesson status`, `/lesson quiz`, `/lesson answer`, `/lesson continue`, `/lesson restart`, and `/lessons` (list) to the Textual desktop app. Full lesson lifecycle now works without the CLI bridge.
- **ApiClient methods:** Added `start_lesson()`, `restart_lesson()`, `record_lesson_attempt()`, `build_quiz()`, and `grade_quiz()` to `frontend/services/api_client.py`. Removed direct `_post` usage from app code.
- **Objective parsing helper:** Extracted `_parse_lesson_objective()` static method to deduplicate status/checkpoint parsing across lesson list, status, continue, and quiz handlers.
- **Lesson checkpoint flow:** Opening a lesson now shows the next checkpoint info. After quiz grading, the attempt is recorded via `POST /lessons/{id}/attempt`, progress is updated, and the next checkpoint is announced.
- **`/lessons` subcommand routing:** Fixed `/lessons start`, `/lessons open`, etc. — the plural form now properly routes to the correct handlers instead of being shadowed by the `/lesson` handler.
- **Help text updated:** Both Web TUI and Textual help text now include the full lesson command set.

### Pipeline Bugfix
- **Fixed `_run_qa_pipeline()` NameError:** Added explicit `base_url: str | None` parameter to `_run_qa_pipeline()` in `step12_foundation/pipeline/main.py`. Previously `settings.deepseek_base_url` was referenced as a bare global, but `settings` was a local variable in `run()` — this would crash at runtime when `--run-qa` was used. The call site now passes `settings.deepseek_base_url` explicitly.

### Web TUI UX
- **Lesson table progress column:** The `/lessons` list now shows a "Progress" column with `currentCheckpoint/totalCheckpoints` for each lesson.
- **Lesson restart from Web TUI:** `/lessons restart <N>` resets a lesson's checkpoint progress via `POST /lessons/{id}/restart`.

### Textual TUI
- **`/lessons` list with progress:** Shows lesson status and checkpoint progress in the chat pane.
- **`/lesson continue` shortcut:** Auto-selects the single in-progress lesson (or lists multiple for manual selection).
- **`/lesson answer` grading flow:** Parses comma-separated letter answers, maps to quiz options, grades via `POST /quiz/grade`, records the attempt, and reports pass/fail with next checkpoint info.

### Code Quality
- **Removed redundant `import re`:** Eliminated three inline `import re as _re` statements inside methods, using the module-level `re` import instead.
- **URL-encoded lesson IDs:** `restart_lesson()` and `record_lesson_attempt()` in `ApiClient` now use `urllib.parse.quote()` for safe URL path construction.
- **Stale tests updated:** `frontend/tests/test_removed_commands.py` expectations updated to match actual behavior — commands like `/settings`, `/dashboard`, `/search` fall through to the chat path (not silently dropped).

## 2026-06-04

### Server-Side Architecture Refactor
- **All business logic moved to backend:** Frontend is now a thin render-only client. No embedding models, quiz grading, semantic search, or pipeline orchestration runs in the browser.
- **New embedding service** (`services/embeddings.py`): Server-side `sentence-transformers` with deterministic hash-based fallback. Embedding model preloaded in background thread at server startup (eliminates 15s cold-start penalty on first request).
- **New endpoint `POST /auth/bootstrap`**: Single call for post-login setup — handles unlock + sync pull + lessons/docs fetch in one request. Replaces the previous unlock → health check → sync → fetch chain that could silently fail.
- **New endpoint `POST /docs/search/text`**: Accepts query text, embeds server-side, runs cosine similarity, returns top-K matching chunks.
- **New endpoint `POST /quiz/grade`**: Accepts quiz + answers, grades server-side using `step12_foundation.quiz.quiz_backend.grade_quiz()`. Returns per-question scores.
- **Chat auto-search:** `POST /chat/sessions/{id}/messages/stream` now performs semantic search server-side when no `doc_chunk_ids` are provided. Frontend just sends the message text.
- **Doc upload auto-embed:** `POST /docs/upload` now generates embeddings server-side after ingestion. No client-side embedding step needed.
- **Pipeline orchestration simplified:** Frontend sends topic info to `/pipeline/generate-topic-qa`; server handles context selection.
- **`.env` auto-loading:** `main.py` now calls `load_dotenv()` at import time, before `db.py` reads `DATABASE_URL`. Fixes cloud DB connectivity when running locally without `run_backend_cloud.ps1`.
- **Engine disposal on shutdown:** `engine.dispose()` called in lifespan shutdown handler to close DB connections cleanly.
- **DSML tag stripping:** `_strip_dsml()` helper ensures raw DeepSeek DSML tool-call markup never leaks to the frontend. Applied in both streaming and non-streaming paths.
- **Error logging:** Replaced silent `except: pass` in `generate_reply_stream` with `logger.warning`. SSE stream errors now logged with full tracebacks.
- **SSE buffer fix:** Frontend SSE reader now processes remaining buffer after stream ends, preventing the last `done` event from being lost.
- **Client-side code removed:** HuggingFace Transformers module, quiz grading algorithms, client-side embedding + search, pipeline embedding orchestration all removed from the Web TUI.

### Hybrid Semantic Search Architecture
- **Server-side vector search:** Added `POST /docs/search` endpoint — accepts a query vector (384 floats from browser embedding), runs cosine similarity against stored chunk vectors in the backend, returns top-K matching chunks. Validates vector length (`len(query_vector) == 384`) before search.
- **Backend search function:** Added `search_chunks_by_vector()` in `storage.py` — loads all docs with vectors via `list_docs()`, computes cosine similarity for each chunk, returns sorted top-K results with scores.
- **Bandwidth optimization:** `GET /docs`, `GET /docs/search`, and `GET /docs/{doc_id}` all strip chunk vectors from the response (chunks sent without `vector` field). Saves ~60KB per 20-chunk document. Shared `_strip_vectors()` helper ensures consistency across all doc endpoints.
- **Frontend rewrite:** `handleTutorChat()` now sends the query embedding (computed on the main thread via Transformers.js) to `POST /docs/search` instead of iterating `_indexedChunks` client-side.
- **Pipeline search rewrite:** `/pipeline generate` topic context selection also uses `POST /docs/search` instead of client-side cosine similarity.
- **Dead code removal:** Removed `_indexedChunks`, `_rebuildChunkIndex()`, `_getCachedQueryEmbedding()`, `_queryEmbeddingCache`, `_QUERY_CACHE_MAX` from the frontend. Kept `cosineSimilarity()` only for quiz grading (still used client-side for answer comparison).
- **Model preload:** Embedding model starts loading during page idle (`requestIdleCallback` or 3s `setTimeout`) instead of on first chat message.
- **Pre-existing bug fix:** Fixed `list_reward_session_logs()` in `storage.py` — `rows` was uninitialized when `db_enabled()` was False, causing `UnboundLocalError` on unknown account dashboard requests. Also restored missing `else` branch for file-mode reward log reading and removed orphaned dead code after the function.

### SSE Streaming for Chat
- **Streaming endpoint:** Added `POST /chat/sessions/{id}/messages/stream` — returns `text/event-stream` (SSE) with token-by-token LLM responses.
- **Backend streaming engine:** Added `_complete_with_tools_stream()` and `generate_reply_stream()` in `chat_engine.py` — first LLM call stays non-streaming (to detect tool calls), followup responses stream via `stream=True` on OpenAI client.
- **Streaming service layer:** Added `send_chat_message_stream()` in `chat_service.py` — yields `{"event": "token|tool|done", "data": "..."}` dicts; accumulates full reply for session history after stream completes.
- **SSE error handling:** `_sse_stream()` catches both `HTTPException` and generic `Exception` — always sends an error SSE event before stream close.
- **Frontend SSE consumer:** `handleTutorChat()` in the Web TUI reads the SSE stream via `ReadableStream` + `reader.read()`, parsing `data:` lines and appending tokens incrementally.
- **Streaming terminal output:** Added `createStreamContainer()`, `appendStreamToken()`, `finalizeStream()` — tokens render as plain text during streaming, then `renderMd()` re-renders with markdown/LaTeX on completion.

### Web TUI UX Overhaul (22 fixes)
#### Auth Flow
- **Back navigation:** Typing `back` at any auth step (username, PIN, confirm, passphrase) returns to the previous step. Passphrase "back" correctly routes to PIN confirm in create flow.
- **Login error recovery:** Invalid PIN no longer clears the username — returns to PIN step with retry prompt.
- **PIN mismatch recovery:** Mismatched PIN confirmation returns to PIN step instead of resetting the entire auth flow.
- **Registration error recovery:** Registration failure now returns to PIN step (preserving username) instead of calling `resetToAuth()`.

#### Chat Experience
- **"Connecting to tutor..." status:** Shown in the stream container while waiting for the first SSE token.
- **Stop generation:** Ctrl+C aborts the active SSE stream mid-response — shows `[Response cancelled]` inline.
- **Command history:** Arrow ↑/↓ cycles through the last 50 commands (shell mode only).
- **Ctrl+C dual purpose:** Aborts active stream if one is running, otherwise clears the input line.

#### Command Discovery
- **Tab autocomplete:** Pressing Tab on a `/`-prefixed partial command fills in the unique match, or lists all matches.
- **Grouped /help:** Commands organized into categories (CHAT, DOCUMENTS, SYNC, SYSTEM, SHORTCUTS) with visual hierarchy.
- **Bottom bar hint:** "Type a message to chat with the tutor" shown in the footer control bar.

#### Document Reader
- **Chunk selection checkboxes:** Reader modal now shows individual chunks with toggle checkboxes for context injection, replacing the full-document text view.
- **Document search:** Search input in the reader header — filters chunks by text match.
- **Reader keyboard nav:** Ctrl+F focuses the reader search; Escape from search clears it and returns focus to terminal.

#### Lesson Panel
- **Explicit Clear button:** Footer shows a "Clear" button when a lesson is active (no more click-to-deselect toggle).
- **Lesson objective display:** Footer shows the lesson objective from content_blocks.
- **Session reset warning:** Switching lessons now warns before resetting the chat session.

#### Error Handling
- **Server error details:** API errors now extract and display the server's `detail` field instead of generic messages.
- **Backend health status:** Footer shows `backend: online` or `backend: offline` — checked every 30s; changes shown as toast notifications.
- **Retry hint:** "Tip: Press ↑ then Enter to retry." shown after chat errors.

#### Visual / Polish
- **Loading state:** Side panels show "Loading..." until the first data fetch completes after login.
- **Toast notifications:** Slide-in toast notifications for sync success/failure and connection changes — replaces inline terminal messages for operational feedback.
- **Toast pointer events:** Toast container has `pointer-events: none` so it doesn't block clicks on underlying UI.

### Code Review Fixes
- **Double reply bug:** `_complete_with_tools_stream()` yielded `("done", content)` which re-sent the full reply — fixed to `("done", "")` so the done event carries no redundant data.
- **SSE stream error handling:** Added `except Exception` catch in `_sse_stream()` for non-HTTP errors.
- **Passphrase back navigation:** Now routes to `ST_PIN_CONFIRM` in create flow instead of always `ST_PIN`.
- **Registration error recovery:** Returns to PIN step instead of `resetToAuth()`.
- **Lesson re-select guard:** Clicking the same lesson no longer resets `chatSessionId`.
- **Health check guard:** Backend health check only runs when `currentState === ST_SHELL`.
- **Logout state reset:** `resetToAuth()` now clears `_initialLoadComplete` and `_indexedChunks` (when it existed — now removed by hybrid search refactor).
- **Chunk ID escaping:** `escapeHtml()` applied to chunk IDs in onclick handlers.
- **Toast pointer events:** Container `pointer-events: none`, toasts `pointer-events: auto`.
- **Redundant assignments:** Removed duplicate `_lastRenderedLessons`/`_lastRenderedDocs` assignments in `resetToAuth()`.
- **Duplicate Ctrl+C feedback:** Removed redundant `print("Response cancelled.")` from Ctrl+C handler — the stream catch block handles UI feedback.
- **Tool reply done event:** `send_chat_message_stream` tool_reply case now yields `{"event": "done", "data": ""}` instead of duplicating the reply text.

### New Web TUI Template
- **Tailwind CSS redesign:** `backend/app/templates/index.html` — complete UI overhaul using Tailwind CSS, Material Icons, Geist/Source Serif fonts, and glassmorphism panels. Replaces the inline monochrome terminal aesthetic.
- **Tab-based layout:** Dashboard, Chat Studio, Doc Study tabs with sidebar navigation. Chat workspace shared between Chat and Doc Study tabs.
- **Login overlay:** Card-based auth flow with mini terminal log, replacing the full-page terminal auth experience.
- **Chat bubbles:** Messages rendered as styled chat bubbles (user right-aligned, assistant left-aligned) instead of plain terminal lines.
- **Streaming support:** All SSE streaming, hybrid search, abort, and toast features integrated into the template.
- **UI fixes:** Removed `docsCache.length > 0` guard in semantic search, added stream separator text, clears stale auth messages on shell transition, added `inputLocked` visual feedback on container, added `sanitizeHtml` on `isHtml` path in `print()`.

### Documentation & Testing (morning)
- **Security Policy:** Added `SECURITY.md` at project root — E2EE architecture, authentication layers, brute-force protection, threat model, vulnerability reporting process.
- **Browser Automation Tests:** Added `backend/tests/test_browser_js.py` with 40 Playwright tests covering grading functions, cosine similarity, MCQ grading, open-answer grading with mock embeddings, and pipeline orchestration with mocked API responses.
- **Test Infrastructure:** Added `backend/tests/conftest.py` with `page` (blank) and `app_page` (live server) Playwright fixtures. Added `backend/tests/README.md` documenting test architecture and how to run.
- **Grading Constants:** Added explicit constants table to `step12_foundation/docs/quiz/QUIZ_BACKEND_ARCHITECTURE.md` (thresholds, weights, generation caps).
- **Embedder Fallback Docs:** Documented embedder initialization fallback behavior and logged warning in `step12_foundation/docs/pipeline/ARCHITECTURE.md`.
- **PR Template:** Updated `.github/pull_request_template.md` — replaced stale Azure SQL/Firebase references with current PostgreSQL/E2EE stack.
- **Endpoint List:** Added missing routes (`/auth/register`, `/auth/login`, `/quiz/build`, `/pipeline/*`, `PUT /docs/{id}/embeddings`) to `backend/README.md`.
- **Root README:** Added `/pipeline generate` to Web TUI commands list.
- **Architectural Doc Refs:** Added `SECURITY.md` and `backend/tests/README.md` to `ARCHITECTURE.md` document references table.

### Security Policy Updates
- **Stale Dependency Fix:** Updated `requirements-security.in` — `uvicorn` 0.35.0 → 0.48.0, `python-multipart` 0.0.20 → 0.0.30 to match actual backend lockfile.
- **Legacy Cleanup:** Moved `docs/studybuddy-backend-prd.md` (584-line StudyBuddy legacy PRD) to `docs/archive/`.

## 2026-06-03

### Code Review & UI Optimizations
- **FastAPI Lifespan Migration:** Migrated the deprecated `@app.on_event("startup")` event handlers in [main.py](backend/app/main.py) to a modern, unified `lifespan` context manager.
- **Unified Quiz Grading:** Removed the client-side grading short-circuit logic in [main.py](backend/app/main.py) to align browser-side grading with the Python backend grading in [quiz_backend.py](engine/quiz/quiz_backend.py) (always blending lexical and semantic scores).
- **JS Cosine Similarity Safety:** Added an epsilon check (`denom <= 1e-12`) to the Web TUI JavaScript `cosineSimilarity` function to prevent division-by-zero errors.
- **Embedding Ingestion Warnings:** Added warning logging in [rag_engine.py](engine/pipeline/rag_engine.py) to surface when the embedding model fails to load and a mock fallback embedder is engaged.
- **Zero-Lag Subprocess TUI Bridge:** Optimized the Textual TUI CLI bridge in [foundation_cli_bridge.py](frontend/services/engine_cli_bridge.py) to block on `read(1)` when idle (eliminating constant 0.2s CPU wakeups) and replaced `splitlines` parsing with direct newline splitting to output subprocess lines instantly without the 0.2s lag.
- **TUI Chat Input Lockout:** Disabled the chat input widget in [app.py](frontend/app.py) during active API messaging calls to prevent race conditions on session creation and out-of-order responses.
- **ORM N+1 Query Optimization:** Optimized database fetch queries in [account_sync.py](backend/app/services/account_sync.py) (`_pull_chat_sessions_from_cloud`, `_pull_reward_sessions_from_cloud`) and [storage.py](backend/app/services/storage.py) (`list_reward_session_logs`) using SQLAlchemy `selectinload` to eager-load nested relationships in a single combined SQL query instead of lazily loading in a loop.

### Vercel-Safe Quiz Flow
- Removed `_quiz_cache` and `/quiz/grade` endpoint — quiz grading is now fully client-side.
- QA artifacts loaded from `AccountArtifactModel` (DB) in cloud mode, with E2EE decryption via `user_dek`.
- `/quiz/build` endpoint supports both DB-backed and local filesystem QA loading.
- Client-side `gradeQuizLocally()` handles MCQ and open-answer grading with batched semantic embeddings.

### Client-Side Semantic Grading
- Open answers graded using browser embeddings (`@huggingface/transformers`, ONNX `all-MiniLM-L6-v2`).
- WebGPU preferred, WASM fallback for embedding computation.
- Blended scoring: `0.7 * semantic + 0.3 * lexical` with fast-path skip when lexical ≥ 0.4.
- All open questions' embeddings batched in a single `embedTextBatch()` call.

### MCQ Distractor Improvements
- Distractors scored by token overlap with correct answer (stop words excluded).
- Fallback chain: "None of the above" → "All of the above" → "Not enough information" → "The question is unclear".
- Infinite-loop guard on fallback generation.

### Question Rephrasing
- Expanded from 3 patterns to 12 (what is, what are, explain why/how, how does/do, describe, which of, when should/to, where is/are, why is/are).
- Trailing verb stripping prevents awkward grammar ("mechanism by which memory allocation", not "memory allocation work").
- Leading determiner/verb stripping for "which of" patterns.
- Punctuation stripping via regex handles `??`, `?!`, etc.

### Embedding Pipeline
- Browser-side document embedding via `@huggingface/transformers` (WebGPU/WASM).
- Embeddings saved to server as `.embeddings.json` companion artifacts in `AccountArtifactModel`.
- Semantic search in tutor chat injects relevant chunk context based on query embedding similarity.
- Lazy model loading — embedding model loads on first use, not at page startup.
- Batch embedding for document uploads.

### Pipeline API
- `POST /pipeline/generate-curriculum` — curriculum extraction via DeepSeek LLM.
- `POST /pipeline/generate-topic-qa` — per-topic QA generation via DeepSeek LLM.
- `POST /pipeline/save` — save QA artifacts to DB or local disk.
- `PUT /docs/{doc_id}/embeddings` — save chunk embeddings to DB.
- Browser orchestrates full flow: embed → generate curriculum → generate QA → save.

### Code Quality
- Centralized `_is_stateless_db_mode()` in `db.py` — removed 4 duplicate definitions.
- Moved `hashlib` imports to module top in `storage.py` and `pipeline.py`.
- Replaced magic `[:-16]` with `.removesuffix(".embeddings.json")`.
- Narrowed `save_embeddings` DB query to documents/raw_text only (not all artifacts).
- Extracted quiz grading helpers to module-level functions in `main.py` (was 145-line nested function).
- All constants extracted with `GRADING_` prefix for clarity.

### Tests
- Added `test_quiz_qa_loading.py` — 9 tests for DB and disk QA loading paths.
- Added `test_embeddings_storage.py` — 1 test for embeddings lifecycle in stateless mode.
- Updated test patches to use centralized `is_stateless_db_mode`.

## 2026-06-02

### Unlock Security Hardening
- Replaced process-local unlock token cache with DB-backed unlock sessions.
- Bound unlock tokens to account id, unlock version, and caller client context.
- Added DB-backed unlock invalidation/versioning via `account_keys.unlock_version`.
- Stopped binding unlock sessions to client IP; binding now uses stable client headers.
- Invalid unlock tokens now fail closed with `401` instead of degrading to ciphertext reads.
- Restricted first-time key envelope creation to existing accounts only.
- Added per-account unlock failure tracking and temporary lockout controls.
- Added focused auth tests for client binding, version invalidation, and rate limiting.
- **Lockout escalation:** Added exponential backoff for repeated lockouts (doubles up to 240 min max). Reset on successful unlock.
- **Concurrent safety:** Replaced read-increment-write unlock failure tracking with `SELECT ... FOR UPDATE` to prevent lost-update races.
- **Token binding enforcement:** Unlock tokens now require `X-Client-Id` header (min 8 chars). Missing or short headers are rejected with `400`.
- **Account ID validation:** Added regex validation (`^[a-zA-Z0-9_-]{3,64}$`) on account_id in all endpoints, rejecting invalid ids before normalization.
- **Expired session cleanup:** Added automatic cleanup of expired `account_unlock_sessions` rows on startup and on every unlock attempt, preventing unbounded table growth.
- **Account existence caching:** Added in-memory TTL cache (30s, bounded at 256 entries) for `_account_exists` lookups to mitigate file I/O on repeated unknown-account unlock attempts.
- **Fernet key derivation:** Upgraded from plain SHA-256 to PBKDF2-HMAC-SHA256 (100k iterations) for deriving Fernet key from `UNLOCK_TOKEN_SECRET`, with instance caching.

### Encryption Coverage
- Extended app-layer encryption coverage beyond chat messages to reward event notes in DB mode.
- Encrypted `quiz_history` and `lesson_progress` in the account registry (`account_registry_records`) at rest in DB mode.
- `_save()` preserves encrypted fields when DEK is unavailable (prevents data loss when the unlock session expires).

### Docs and Config
- Added unlock security env vars to `.env.example`.
- Updated root/backend docs for `X-Client-Id`, unlock token invalidation, and lockout behavior.

### Sync Security Refactor
- **Shared unlock guard:** Extracted passphrase-based DEK resolution into `backend/app/services/unlock_guard.py` with lockout/failure accounting shared by `/auth/unlock` and admin sync scripts.
- **Backend sync API:** Added `POST /sync/push` and `POST /sync/pull` endpoints requiring `X-Unlock-Token` + `X-Client-Id`, reusing the existing unlock session model.
- **DEK-only sync functions:** `push_account_to_cloud` and `pull_account_from_cloud` now accept DEK bytes directly instead of raw passphrase — eliminating a side door around unlock policy.
- **Frontend sync via API:** F7/F8 now route through an unlock modal (passphrase → `/auth/unlock` → token in-memory) then call `/sync/push` or `/sync/pull` directly. No more PowerShell subprocess or `ACCOUNT_SYNC_PASSPHRASE` requirement for users.
- **Admin scripts preserved:** `run_cloud_sync.ps1` and `run_cloud_pull.ps1` remain for operator recovery, now using shared unlock guard for passphrase validation with lockout tracking.
- Added 10 sync-specific tests covering: missing/invalid token rejection, bad passphrase rejection, failure counter, lockout enforcement, and DEK resolution with counter reset.

### Web TUI Rewrite
- Reordered panels to match Textual TUI layout: `Lessons | Terminal | Documentation`.
- Added **Settings modal** (F5) — view and edit the AI tutor system prompt inline.
- Added **Dashboard modal** (F6) — summary stats, SVG bar chart of quiz score trend, recent quizzes, and session logs.
- Added **inline MCQ quiz** (`/quiz` command) — interactive multiple-choice questions with click-to-select answers, Prev/Next navigation, arrow key support, and LaTeX/Markdown rendering via KaTeX + marked.js.
- Added **document reader modal** with full-screen view, chunk list, and chunk selection.
- Added **terminal footer** — live message count, last sync timestamp, lock/unlock status.
- Added **cloud/local status** on login (`/health/db` check).
- **Auto-pull on login** — pulls latest cloud data when connected; manual F2/F3 still available.
- Terminal log capped at 500 entries to prevent DOM bloat.
- CSS: modern dark theme with JetBrains Mono, responsive breakpoints.
- XSS sanitization: `sanitizeHtml()` strips scripts, event handlers, and `javascript:` URLs.
- `/lock` is immediate; no longer blocks on network sync.
- Settings form preserves text on validation/save errors (inline error display).
- Locked panels show subtle `locked` indicator instead of blank void.

### Backend – Quiz API
- Added `POST /quiz/build` and `POST /quiz/grade` endpoints using existing `build_quiz()` / `grade_quiz()` from foundation pipeline.
- Quiz data cached in-memory by `quiz_id`; supports topic, difficulty, mode, and count filters.

### Backend – Lesson API
- `/lessons` endpoints now accept optional `X-Unlock-Token` for decrypting encrypted `lesson_progress`.
- `Lesson` schema extended with `checkpoints` field (`LessonCheckpoint` list) showing per-checkpoint title, difficulty, question count, and pass threshold.

### Backend – Sync Safety
- `push_account_to_cloud()` now defaults to `delete_stale=False` — API sync is additive and never deletes cloud-only artifacts (e.g. docs uploaded via Web TUI).
- `run_cloud_sync.ps1` passes `delete_stale=True` for authoritative full sync with deletion.
- `pull_account_from_cloud()` gracefully skips artifacts it cannot decrypt.
- Cross-account chat session default changed from `"admin"` to the requesting `account_id`.

### Code Review Fixes (2026-06-02)

**Account Seeding & Vercel Boot**
- Fixed silent `except Exception: pass` that swallowed all DB errors during local→cloud account seeding (`account_system.py:_load()`). Now logs full tracebacks.
- Changed `DB_AUTO_CREATE_SCHEMA` default from `""` to `"true"` so tables auto-create on Vercel startup without manual Alembic.
- Fixed `account_exists()` (unlock_guard.py) to query the cloud DB (`AccountRegistryRecordModel`) before falling back to local JSON — accounts created via `/register` on Vercel are now found during `/unlock`.
- `migrate_file_data_to_db.py` and `export_db_to_file_data.py` now bootstrap `AccountKeyModel` if absent (auto-create key material envelope on first sync).
- `list_account_lessons()` logs failures via `logger.warning` instead of returning empty list silently.

**Data Integrity**
- **Fixed data-loss bug in `_save()`**: When DEK was unavailable, the preservation logic overwrote fresh in-memory `quiz_history`/`lesson_progress` with stale encrypted DB blobs. Now only preserves when the field is truly absent from the profile.
- **Fixed sync push deleting cloud-only docs**: `push_account_to_cloud` no longer deletes DB artifacts by default (`delete_stale=False`). Docs uploaded via the Web TUI on Vercel are safe from sync push.
- **Pull gracefully skips undecryptable artifacts**: `pull_account_from_cloud` catches decryption failures and continues — legacy/corrupted artifacts are no longer fatal.
- Decrypt failures in `_load()`, `_save()`, and `pull_account_from_cloud()` now log tracebacks instead of silently passing.

**SHA-256 Integrity**
- `add_doc()` now stores the SHA-256 hash of the **plaintext** content (not ciphertext), matching `push_account_to_cloud`.
- `_read_stateless_docs` verifies the SHA-256 hash after decryption, skipping tampered documents.

**Config & Wiring**
- Added `DEEPSEEK_BASE_URL` env var (defaults to `https://api.deepseek.com`) — configurable in `.env` and on Vercel.
- Fixed `Settings` dataclass missing `deepseek_base_url` field — would crash `chat_engine.py` and `pipeline/main.py` at runtime.
- All three `QuizIntentRouter` / `CurriculumQAPipeline` instantiations pass `base_url` from config.

**Render & UI State**
- Fixed `renderAllPanels()` not updating on selection changes. Added `_forceRender` flag; `selectLessonByIndex`, `toggleChunk`, `openDocReader` now force re-render.
- `runSyncPush()` and `runSyncPull()` now re-throw after printing — callers can detect failure via `await`/`catch`.
- `/clear` now resets `msgCount` and calls `updateTerminalFooter()`.
- `resetToAuth()` now clears `passphrase`, `lessonsCache`, `docsCache`, and render snapshots.
- Polling interval extended from 5s to 15s.
- Removed duplicate error print on auto-pull failure.
- Removed duplicate `closeDocReader()` call in `resetToAuth()`.

**Tests**
- Added `AccountStoreDbSaveRegressionTests` — verifies `_save()` without DEK preserves fresh quiz data (does not overwrite with stale encrypted blobs).
- Updated `test_account_sync.py` to reflect non-destructive push default and graceful pull fallback.

**Documentation**
- Added root `ARCHITECTURE.md` — full system design with ASCII diagrams, data flow, E2EE chain, DB schema, and security model.
- Updated `README.md` with Web TUI key bindings, commands, sync behavior, and `DEEPSEEK_BASE_URL` config.

## 2026-06-01

### Cloud/Local Database Modes and Operations
- Added dual database runtime support:
  - local-first mode (`run_backend.ps1`) that clears DB env vars
  - cloud DB mode (`run_backend_cloud.ps1`) using `.env` `DATABASE_URL`
- Added Supabase transaction-pooler-safe DB engine behavior:
  - `postgresql://` normalization to `postgresql+psycopg://`
  - `NullPool` + `prepare_threshold=None` for `pooler.supabase.com:6543`
  - SQLite compatibility mode (`check_same_thread=False`)
- Added `GET /health/db` connectivity endpoint.
- Added explicit cloud data operations:
  - `run_cloud_sync.ps1` (local file data -> cloud DB with Alembic migrate)
  - `run_cloud_pull.ps1` (cloud DB -> local `backend/data` export)
- Added Alembic migration scaffolding and revisions for backend SQL schema.

### Backend Persistence and Data Safety
- Migrated chat/rewards persistence paths to SQL-capable storage while retaining file fallback when DB is disabled.
- Added account-scoped session behavior for chat APIs in DB mode.
- Added unique chat sequence constraint and retry flow for concurrent message writes.
- Preserved account ownership and reward timestamps in file<->DB migration/export scripts.
- Sanitized `/health/db` error detail by default (`DB_HEALTH_VERBOSE_ERRORS` enables verbose output).

### Application-Layer Encryption (Initial Slice)
- Added local cryptography service:
  - Argon2id passphrase -> KEK derivation
  - per-account DEK generation/wrapping
  - AES-GCM text encryption/decryption helpers
- Added account key envelope storage table (`account_keys`) and migration.
- Added unlock/lock endpoints:
  - `POST /auth/unlock`
  - `POST /auth/lock`
- Added short-lived unlock token cache and optional `X-Unlock-Token` request handling.
- Wired encrypted-at-rest behavior for chat message text in DB mode (decrypt-on-read when unlocked).

### Frontend Operations UX
- Added compact footer commands for operational actions:
  - `Check` (`F6`)
  - `Sync` (`F7`)
  - `Pull` (`F8`)
- Kept `Settings` (`Ctrl+S`) and `Dashboard` (`Ctrl+D`) in footer command row.
- Routed cloud sync/pull/check execution output into chat transcript for operator visibility.

### Backend Settings and Prompt Controls
- Added account-scoped settings API:
  - `GET /settings`
  - `PATCH /settings`
- Added account-scoped chat system prompt storage and fallback chain:
  - `step12_foundation/accounts/{account_id}/config/chat_system_prompt.md`
  - `backend/prompts/chat_system_prompt.md`
  - built-in default
- Added chat tool commands for settings management:
  - `/settings get`
  - `/settings set system_prompt <...>`

### Textual Frontend UX and CLI Bridge
- Added Textual frontend app wiring for horizontal `Lesson | Chat | Doc`.
- Kept center pane behavior aligned with foundation CLI by bridging `step12_foundation.quiz.quiz_chatbot_cli` subprocess output/input.
- Fixed CLI prompt visibility in UI:
  - enabled unbuffered subprocess mode
  - moved heavy embedding preload to after login in CLI startup
  - improved prompt/line streaming behavior for non-newline `input(...)` prompts
- Added bridge lifecycle/status feedback in chat pane:
  - startup
  - ready
  - unexpected exit code
- Reduced side-pane flicker by diffing backend refresh snapshots before repaint.
- Improved side-pane layout/readability:
  - wider center pane
  - doc/chunk list sizing adjustments
  - full selected chunk rendering in scrollable doc detail area
- Added separate Settings modal window (instead of in-layout pane) to preserve main workspace width.
- Added Settings entry points:
  - bottom-left `Settings` button
  - `/settings` command
  - `Ctrl+S` shortcut
- Hardened pre-login settings behavior to avoid default account prompt exposure by gating settings load/save until sign-in.
- Tightened settings intent routing:
  - slash command remains primary (`/settings`)
  - only explicit settings-management phrases auto-open the settings window
  - generic chat messages are no longer broadly intercepted

### Security
- Patched backend dependency vulnerability by upgrading `python-multipart`:
  - `backend/requirements.txt`: `0.0.20` -> `0.0.27`
- Added hash-locked dependency files for backend and frontend:
  - `backend/requirements.lock.in` / `backend/requirements.lock.txt`
  - `frontend/requirements.lock.in` / `frontend/requirements.lock.txt`
- Expanded dependency security workflow coverage:
  - now triggers on `backend/**` and `frontend/**` changes
  - added hash-verified install + audit steps for backend/frontend lockfiles
  - retained existing `step12_foundation` hash-verified audit path

## 2026-05-31

### Account-Scoped Storage
- Added strict account-id validation and shared account path resolver.
- Switched pipeline runtime paths to account-scoped directories:
  - `documents`, `cache`, `graph_db`, `output`
- Added `--account-id` to pipeline CLI and account-scoped defaults for `--input-dir` and `--qa-output`.
- Migrated legacy data into `step12_foundation/accounts/admin/...`.
- Moved account registry to `step12_foundation/accounts/accounts.json`.

### Context and Caching
- Added shared hybrid context builder and reused it across QA generation and quiz chat routing/tutoring prompts.
- Added cache metrics tracking:
  - `prompt_cache_hit_tokens`
  - `prompt_cache_miss_tokens`
- Added context prefix fingerprint metadata for cache-stability diagnostics.

### Pipeline Fixes
- Fixed `--qa-output` optional-path crash in QA pipeline execution path.
- Fixed raw-text overwrite risk in folder runs by fanning out `.txt` output targets to per-document files.

### Lesson UX and Routing
- Added lesson library/navigation APIs:
  - list lessons
  - set active lesson
  - restart lesson
- Added lesson metadata fields (`created_at`, `last_accessed_at`) and migration backfill.
- Added chatbot commands and selectors for lesson navigation:
  - `lessons`
  - `resume <n|lesson_id>`
  - `restart lesson <n|lesson_id>`
- Kept `open lesson <...>` as a backward-compat alias (not surfaced in UX copy).
- Added top-level tool-calling intent routing for lesson/dashboard actions.
- Prevented `start lesson` without topic from falling into generic quiz build flow.

### Tests and Documentation
- Added tests for account path normalization/isolation and lesson command parsing/selection.
- Updated `README.md` and `step12_foundation/README.md` for account-scoped storage and revised lesson UX.
- Maintained green test suite after refactors and behavior changes.

## 2026-05-30

### Pipeline and Backend
- Added backend abstraction with `jsonl`, `lightrag`, and reserved `web` backend mode.
- Added effective-backend handling with optional strict mode (`--backend-strict`).
- Updated QA path to consume rows from the active backend adapter instead of hardcoded index files.
- Hardened ingestion cache validation using per-chunk hashes and backend-aware manifest metadata.
- Fixed stale-row handling for empty documents by clearing existing backend rows on empty ingest.
- Improved LightRAG adapter behavior:
  - preserves chunk text/hash snapshots for QA retrieval
  - validates document presence before cache-skip
  - supports empty-row delete path
  - surfaces initialization failure reasons in strict mode

### Quiz
- Added typo-tolerant grading for open answers and typed MCQ option text matching.
- Added tests for typo acceptance behavior.

### Dependency and CI Security
- Added `step12_foundation/requirements.in` and hash-locked `step12_foundation/requirements.txt`.
- Enforced `pip install --require-hashes` in docs and CI.
- Added security workflow: `.github/workflows/step12-foundation-security-ci.yml`.
- Added hash-locked CI tooling files:
  - `step12_foundation/requirements-security.in`
  - `step12_foundation/requirements-security.txt`

### Repo Cleanup
- Removed legacy `studybuddy-api` source/test files and its CI workflow.
- Removed legacy StudyBuddy backend draft docs.
- Updated PR template to use generic verification checks.
