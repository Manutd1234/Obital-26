# Backend (Lesson | Chat | Doc)

This backend exposes API endpoints for the `Lesson | Chat | Doc` interface.
Lessons are loaded on demand, so they are only active when a lesson endpoint is called.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.lock.txt
.\.venv\Scripts\python -m uvicorn backend.app.main:app --reload --port 8000
```

Local mode uses SQLite at `backend/data/obital.db` with full E2EE encryption. No database configuration required.

Optional Postgres mode (recommended for hosting):

```powershell
$env:DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/obital"
.\.venv\Scripts\python -m uvicorn backend.app.main:app --reload --port 8000
```

Local-first default:

- `../scripts/dev/run_backend.ps1` clears DB env vars and runs in local SQLite mode.
- `../scripts/dev/run_backend_cloud.ps1` loads `.env` and runs against `DATABASE_URL`.

Health checks:

- `GET /health` (API process alive)
- `GET /health/db` (DB connectivity and enablement status)

## Storage Modes

The app has three storage modes based on environment variables:

| Mode | `LOCAL_DATABASE_URL` | `DATABASE_URL` | Behavior |
|------|---------------------|----------------|----------|
| **SQLite local** | Set to `sqlite:///...` | Empty | Local SQLite DB, plaintext storage |
| **SQLite + cloud sync** | Set to `sqlite:///...` | Set to Postgres URL | Local SQLite + periodic sync to cloud |
| **Postgres direct** | Empty | Set to Postgres URL | Single Postgres instance (Vercel/Supabase) |

SQLite mode uses WAL journaling for concurrent read access. In SQLite mode, data is stored as plaintext locally — encryption only happens during cloud sync.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LOCAL_DATABASE_URL` | Local SQLite database path | Empty (disabled) |
| `DATABASE_URL` | Cloud Postgres database (for Vercel/hosting and sync target) | Empty (disabled) |
| `SYNC_INTERVAL_MINUTES` | Auto-sync interval in minutes (0 = disabled) | `0` |
| `DEEPSEEK_API_KEY` | LLM API key | Required |
| `UNLOCK_TOKEN_SECRET` | Secret for E2EE unlock sessions | Required |
| `DB_AUTO_CREATE_SCHEMA` | Auto-create tables on startup | `true` |
| `UNLOCK_TOKEN_TTL_MINUTES` | Token TTL | `30` |
| `UNLOCK_MAX_FAILED_ATTEMPTS` | Failed attempts before lockout | `5` |
| `UNLOCK_LOCKOUT_MINUTES` | Initial lockout duration | `15` |
| `OBITAL_MAX_UPLOAD_MB` | Max upload file size in MB | `20` |
| `OBITAL_LOCAL_SINGLE_USER` | Set to `1` to disable token requirement | Empty (disabled) |
| `OBITAL_LOG_LEVEL` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `OBITAL_LOG_FILE` | Log file path | `backend/data/logs/backend.log` |

## Schema migrations (Alembic)

Run schema migrations with a migration-safe URL (avoid transaction pooler for DDL):

```powershell
$env:DATABASE_MIGRATION_URL="postgresql+psycopg://postgres:postgres@localhost:5432/obital?sslmode=require"
.\.venv\Scripts\python -m alembic -c backend\alembic.ini upgrade head
```

- `DATABASE_MIGRATION_URL` falls back to `DATABASE_URL` if not set.
- `DB_AUTO_CREATE_SCHEMA=false` by default. Set `true` only for local quickstart behavior.

## DEK mismatch recovery (cloud Postgres)

The account registry stores the profile as JSON, with sensitive fields encrypted
using the account's data-encryption key (DEK). A previous session could retain a
DEK after the account key material had changed. That produced intermittent
failures: the dashboard and lessons were usually the first screens to touch
`quiz_history` and `lesson_progress`, so they appeared to fail together.

The fix has three parts:

1. Unlock sessions store a fingerprint of the account key material (`salt`,
   Argon2 parameters, and wrapped DEK). Requests reject sessions whose
   fingerprint or unlock version no longer matches the current cloud row.
2. Authenticated account reads and writes are scoped to the requesting account.
   Legacy learning fields are migrated before a write, and a failed decryption
   raises `ACCOUNT_KEY_MISMATCH` instead of silently replacing ciphertext with
   empty state. Bootstrap converts this to HTTP `409`, allowing the client to
   discard the stale session and re-unlock.
3. If the original DEK is unrecoverable, the destructive reset tool clears only
   the unrecoverable profile/learning fields while preserving the account,
   documents, chats, and cached counters. It creates a JSON backup first.

For direct cloud Postgres deployment, apply the schema migration before
restarting the backend:

```powershell
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```

For a test account whose old DEK cannot be recovered:

```powershell
.\.venv\Scripts\python.exe scripts\sync\reset_cloud_learning_state.py --account-id admin --dry-run
.\.venv\Scripts\python.exe scripts\sync\reset_cloud_learning_state.py --account-id admin --apply
```

Use `--all` only when every cloud account is intentionally being reset; run its
dry-run first. The reset is not a decryption or recovery operation—it discards
the specified unrecoverable fields so future writes use the current DEK.

### Temporary Vercel lesson-job storage

On Vercel, lesson-generation job state is temporarily written to
`/tmp/obital/lesson_generation_jobs.json` because the deployed `/var/task`
filesystem is read-only. The chat stream stays open on Vercel while the user
waits for the lesson to finish.

This is an online-only workaround, not durable background processing:

- `/tmp` can be cleared on cold starts or redeploys.
- It is local to one serverless instance, so another request may not see it.
- Closing the browser or losing the stream can stop the daemon thread.
- Vercel function timeouts still apply.
- Retries may create duplicate lesson jobs.

The durable replacement is a Postgres-backed job table with a separate
persistent worker. Do not rely on the temporary file for offline processing or
long-running lesson generation.

One-time data migration from file storage to Postgres:

```powershell
$env:DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/obital"
.\.venv\Scripts\python -m backend.scripts.migrate_file_data_to_db --account-id admin --passphrase "<account passphrase>"
```

Current sync direction:

- `../scripts/sync/run_cloud_sync.ps1 -AccountId <id>` / `backend.scripts.migrate_file_data_to_db --account-id <id>`: account-scoped local file and workspace sync -> SQL DB.
- `../scripts/sync/run_cloud_pull.ps1 -AccountId <id>` / `backend.scripts.export_db_to_file_data --account-id <id>`: account-scoped SQL DB -> local file and workspace sync.
- Sync requires the account passphrase, either via `-Passphrase` / `--passphrase` or `ACCOUNT_SYNC_PASSPHRASE`.
- **Passphrase is validated before any data writes** via the shared unlock guard (same lockout/failure tracking as `/auth/unlock`).
- These scripts are intended for **admin/recovery** use. For user-facing sync, use `F7`/`F8` in the frontend which calls `/sync/push` and `/sync/pull` via the unlock token model.

In SQLite mode, sync reads/writes between local SQLite and the cloud Postgres database (configured via `DATABASE_URL`). In Postgres mode, sync is not needed — data is stored directly in the cloud.

The account sync includes:

- `engine/accounts/<account_id>/documents`
- `engine/accounts/<account_id>/output`
- `engine/accounts/<account_id>/config`
- `engine/accounts/<account_id>/cache`
- `engine/accounts/<account_id>/graph_db`
- the selected account record in `engine/accounts/accounts.json`

Cloud sync stores these account artifacts encrypted with the same per-account DEK used by the unlock flow.

## Endpoints

- `GET /health`
- `GET /health/db`
- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/unlock`
- `POST /auth/lock`
- `POST /auth/bootstrap` — single endpoint for post-login setup (unlock + sync + data fetch)
- `POST /sync/push`
- `POST /sync/pull`
- `GET /sync/status` — returns hydration status for the account
- `GET /lessons` — returns lessons with structured metadata (`status`, `current_checkpoint`, `total_checkpoints`). Supports `limit` (1–100, default 20) and `offset` (≥0) pagination.
- `GET /lessons/resolve?q=...` (natural-language lesson lookup, e.g. `lesson on tree`)
- `GET /lessons/{lesson_id}`
- `POST /lessons` — start a new lesson on a topic (generates 3 default checkpoints: fundamentals, application, mastery)
- `POST /lessons/generate` — background lesson generation via harness (returns job immediately, poll status or stream via SSE)
- `GET /lessons/jobs` — list generation jobs for account
- `GET /lessons/jobs/{job_id}` — get job status, subtasks, and result
- `POST /lessons/jobs/{job_id}/cancel` — cancel a running generation job
- `POST /lessons/{lesson_id}/attempt` — record a checkpoint attempt (quiz_id, score_percent, passed); advances checkpoint on pass
- `POST /lessons/{lesson_id}/restart` — reset lesson checkpoint progress
- `POST /lessons/{lesson_id}/checkpoints/{index}/grade` — grade checkpoint answers against embedded quiz data
- `DELETE /lessons/{lesson_id}` — soft-delete lesson (moves to bin with 30-day TTL)
- See `backend/LESSON_HARNESS.md` for architecture details on background generation, parallel workers, and cancellation.
- `GET /docs` — list documents. Supports `limit` (1–100, default 20) and `offset` (≥0) pagination.
- `GET /docs/{doc_id}`
- `GET /docs/search?q=...`
- `POST /docs/search` — vector similarity search (accepts query vector)
- `POST /docs/search/text` — text-based search (embeds query server-side, then searches)
- `POST /docs/upload` — upload document (embeddings generated server-side in background). Max file size: 20 MB (configurable via `OBITAL_MAX_UPLOAD_MB`).
- `DELETE /docs/{doc_id}` — soft-delete document (moves to bin with 30-day TTL)
- `PUT /docs/{doc_id}/embeddings` — **DEPRECATED** (embeddings now auto-generated on upload)
- `GET /bin` — list bin items with days remaining
- `POST /bin/{bin_id}/restore` — restore document or lesson from bin
- `DELETE /bin/{bin_id}` — permanently delete bin item
- `POST /quiz/build` — rate-limited (5 req/min per account)
- `POST /quiz/grade` — grade quiz answers server-side
- `POST /pipeline/generate-curriculum` — rate-limited (2 req/min per account)
- `POST /pipeline/generate-topic-qa` — rate-limited (2 req/min per account)
- `POST /pipeline/save` — rate-limited (2 req/min per account)
- `POST /pipeline/jobs/qa-generation` — create background QA generation job
- `GET /pipeline/jobs/{job_id}` — poll QA generation job status
- `POST /chat/sessions`
- `GET /chat/sessions` — list last 20 chat sessions with titles
- `POST /chat/sessions/{session_id}/messages`
- `POST /chat/sessions/{session_id}/messages/stream` — SSE streaming chat (auto semantic search when no chunk IDs provided)
- `POST /chat/sessions/{session_id}/confirm` — confirm or cancel pending agent tool action
- `GET /chat/sessions/{session_id}`
- `GET /settings`
- `PATCH /settings`
- `GET /rewards/dashboard`
- `POST /rewards/sessions`
- `POST /rewards/sessions/{session_id}/events`

## Application-Layer Encryption (DB mode)

All DB modes (SQLite local and Postgres cloud) encrypt sensitive data at the application layer using per-account keys before writing to the database. Current coverage:

- chat message text
- reward event notes
- user-tutor system prompts (`config/chat_system_prompt.md` files in artifacts table)
- user settings config (`config/settings.json` files in artifacts table)
- private user registry history (`quiz_history` and `lesson_progress` in account registry table)

Flow:

1. Call `POST /auth/unlock?account_id=...` with:

```json
{ "passphrase": "your-passphrase" }
```

2. Use returned token in subsequent chat requests:

```http
X-Unlock-Token: <token>
X-Client-Id: <stable-client-id>
```

3. Call `POST /auth/lock?account_id=...` with the same headers to invalidate the active unlock token version.

Notes:

- First unlock for an account initializes key envelope material (`account_keys`).
- `X-Client-Id` header (min 8 chars) is **required** for `/auth/unlock`; token binding uses the same client context.
- Unlock sessions are stored in the DB and work across multiple workers/instances.
- Unlock tokens are bound to the `X-Client-Id` used during unlock; mismatched client context rejects the token.
- Repeated failed unlock attempts are rate-limited per account with exponential backoff lockout (up to 240 min max).
- Successful unlock resets the failure counter and lockout state.
- Bad unlock tokens fail the request with `401`.
- If no unlock token is supplied, encrypted fields are not decrypted for that request (except sync endpoints which require a token).
- New key envelope material is only created for existing accounts.

Interactive API documentation is available at:

- `GET /api/docs` (Swagger UI)
- `GET /api/redoc` (ReDoc)
- `GET /api/openapi.json` (raw OpenAPI schema)

All lesson, doc, chat-message, settings, pipeline, quiz, rewards, and usage routes are scoped to an account via the `?account_id=` query parameter (required, no default). In cloud/DB mode, a valid `X-Unlock-Token` header is **required** on every account-scoped route; the token is validated against the DB and bound to the account. In local single-user mode (`OBITAL_LOCAL_SINGLE_USER=1`), the token is not required and account identity defaults to the caller-supplied value. Account IDs are format-validated by `normalize_account_id()`: 2–64 chars, lowercase letters, digits, `_`, `-`; must start with a letter or digit; must be lowercase. Invalid IDs return `422`. Non-existent but well-formed IDs are accepted and yield empty data; endpoints will not raise `404` for the account itself.

## Rate limiting

LLM-driving endpoints are rate-limited per account to prevent cost amplification and DoS:

| Endpoint group | Bucket | Limit | Window |
|---------------|--------|-------|--------|
| `/pipeline/*` | `pipeline` | 2 req | 60s |
| `/quiz/build` | `quiz_build` | 5 req | 60s |

Rate limits are enforced via in-memory sliding-window counters keyed by `account_id`. Exceeding the limit returns `429` with a `Retry-After` header.

## LLM request bounds

Pipeline endpoints enforce hard caps on request body sizes to bound per-call LLM cost:

| Field | Max |
|-------|-----|
| `global_context` (curriculum) | 200,000 chars |
| `context_chunks` (topic QA) | 50 items |
| individual chunk | 10,000 chars |
| `max_topics` | 12 |
| `qa_per_topic` | 10 |

LLM calls are wrapped in `asyncio.wait_for` with per-endpoint timeouts:

| Operation | Timeout |
|-----------|---------|
| Curriculum generation | 120s |
| Topic QA generation | 90s |
| Auto-generate QA (quiz fallback) | 180s |

Timeouts return `504`. All LLM errors return generic client messages; details are logged server-side only.

## Upload limits

File uploads are capped at the ASGI layer (middleware) and at the route level (streaming read with incremental size check). Default max is 20 MB per file, configurable via `OBITAL_MAX_UPLOAD_MB`. Exceeding the limit returns `413`.

## Pagination

List endpoints support `limit` and `offset` query parameters:

| Endpoint | `limit` | `offset` | Default `limit` |
|----------|---------|----------|-----------------|
| `GET /lessons` | 1–100 | ≥0 | 20 |
| `GET /docs` | 1–100 | ≥0 | 20 |

Responses are sliced from the full list; no cursor-based pagination.

## Error responses

`POST /chat/sessions/{session_id}/messages` may return:

- `400` if the message is empty or whitespace only
- `404` if the session id does not exist
- `404` if a `lesson_id` is provided but no matching lesson is found

`POST /docs/upload` may return:

- `400` if the upload is empty
- `413` if the file exceeds the size limit (default 20 MB)

`POST /rewards/sessions/{session_id}/events` may return:

- `404` if the reward session log does not exist

`POST /auth/unlock` may return:

- `400` if `X-Client-Id` header is missing or too short
- `401` if the passphrase is invalid
- `404` if the account does not exist
- `429` if the account is temporarily locked due to repeated failed attempts

`POST /sync/push` and `POST /sync/pull` may return:

- `400` if DB mode is not enabled
- `401` if `X-Unlock-Token` is missing, invalid, or expired
- `404` if the account has no key material
- `409` if sync detects a recoverable account-data integrity problem, such as an artifact hash mismatch or invalid registry payload
- `500` if an unexpected server-side sync error occurs

`POST /pipeline/generate-curriculum` and `POST /pipeline/generate-topic-qa` may return:

- `400` if request body exceeds bounds (context too large, topics/QA count out of range)
- `401` if `X-Unlock-Token` is missing or invalid (cloud mode)
- `429` if rate limit exceeded (2 req/min per account)
- `500` if LLM generation fails (generic message; details logged server-side)
- `504` if LLM call exceeds timeout (120s curriculum, 90s topic QA)

`POST /quiz/build` may return:

- `401` if `X-Unlock-Token` is missing or invalid (cloud mode)
- `404` if no curriculum QA data exists and auto-generation produces no documents
- `429` if rate limit exceeded (5 req/min per account)

`POST /quiz/grade` may return:

- `422` if `answers` is missing required fields (`question_id`, `answer`)

## Chat system prompt

The chat model system prompt is resolved in this order:

- `engine/accounts/{account_id}/config/chat_system_prompt.md` (account-specific)
- `backend/prompts/chat_system_prompt.md`

If neither file is present (or non-empty), the backend uses a built-in default prompt.

## Settings API

- `GET /settings?account_id=admin` returns effective settings for an account.
- `PATCH /settings?account_id=admin` updates account settings.

Example:

```json
{
  "chat": {
    "system_prompt": "You are concise and strict about citing context."
  }
}
```

## Chat tool commands

The chat endpoint supports built-in settings tool commands:

- `/settings get`
- `/settings set system_prompt <new prompt>`

These commands are sent to `POST /chat/sessions/{session_id}/messages` as normal chat messages and update account-scoped settings.

## Data storage

Pipeline-backed data source:

- `engine/accounts/{account_id}/output/output_curriculum_qa.json` (lessons)
- `engine/accounts/{account_id}/output/raw_text/*.txt` (docs)
- `engine/accounts/{account_id}/documents/*` (uploaded docs)

Session storage behavior:

- If `LOCAL_DATABASE_URL` or `DATABASE_URL` is set: all data stored in the configured database.
- If neither is set: legacy file storage is used:
  - `chat/sessions.json`
  - `rewards/{account_id}/reward_session_*.jsonl`

Rewards dashboard behavior:

- For unknown but well-formed account IDs, `GET /rewards/dashboard` returns an empty/default payload (zeros and no trend), consistent with account-scoped empty-data behavior in other APIs.

## Package security

Backend dependencies are installed from the hash-locked requirements file:

- `backend/requirements.lock.txt`

Security tooling is tracked separately in the engine package set:

- `engine/requirements-security.in`
- `engine/requirements-security.txt`

The CI workflow enforces hash-verified installs and runs `pip-audit` against the pinned dependency sets in:

- `.github/workflows/engine-security-ci.yml`
