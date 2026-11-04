# Obital — Privacy-first, gamified AI learning platform

[![Core Quality CI](https://github.com/Asdao/Obital/actions/workflows/quality-tests.yml/badge.svg?event=pull_request)](https://github.com/Asdao/Obital/actions/workflows/quality-tests.yml)

Obital turns a learner’s own material into grounded tutor conversations, curated lessons, quizzes, focus progress, XP, ranks, and cosmetic rewards. It is offline-first, supports optional encrypted cross-device sync, and is designed as one connected study loop rather than a collection of unrelated features.

```text
Upload a source -> ask Chat -> create a Lesson -> complete a Quiz
-> earn XP and rank -> use rewards in the Shop -> return to study
```

## Final-phase direction: polish over feature count

The project has reached its intended feature breadth for this milestone. The final phase therefore prioritizes a cohesive UI, reliability, compatibility, and adversarial QA over building a separate mobile app, TTS, or more game modes.

The release goal is simple: the implemented features should work together cleanly and be robust enough to demonstrate proudly on different browsers, devices, and operating systems. New features remain deferred until the core journey passes the release gates in [PLAN.md](docs/planning/PLAN.md).

## Core documentation

The six documents below are the maintained project source of truth:

| Document | Purpose |
|---|---|
| [ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md) | Components, deployment, storage, and security architecture |
| [DATA_PROCESSING_FLOW.md](docs/architecture/DATA_PROCESSING_FLOW.md) | Data lifecycle from upload to grounded learning and rewards |
| [PLAN.md](docs/planning/PLAN.md) | Final-phase priorities, compatibility matrix, and release gates |
| [PRD.md](docs/planning/PRD.md) | Product goals, journeys, requirements, and success measures |
| [TECH_STACK.md](docs/planning/TECH_STACK.md) | Technology choices, boundaries, and compatibility targets |
| [workflow.md](docs/planning/workflow.md) | User journeys, failure recovery, QA missions, and release checklist |

Additional references include [TESTING.md](docs/testing/TESTING.md), [CODING_STANDARDS.md](docs/engineering/CODING_STANDARDS.md), [SECURITY.md](SECURITY.md), [backend/README.md](backend/README.md), [frontend/README.md](frontend/README.md), and the engine documentation under `engine/docs`.

## The connected product

| Screen | Role in the study loop | Natural next step |
|---|---|---|
| **Docs** | Uploaded source material: PDFs, photos, text, Markdown, and extracted passages | Search/read, mark passages, or attach a document in Chat with `@` |
| **Chat** | Grounded AI tutor and the entry point for lesson/quiz creation | Ask a question, then convert the topic into a Lesson or Quiz |
| **Lessons** | Curated AI notes with sources, checkpoints, and embedded quizzes | Learn, complete the checkpoint, and earn progress |
| **Dashboard/Profile** | XP, accuracy, curriculum, focus time, and account status | Review progress and choose the next study action |
| **Workspace** | Focus timer, goals, calendar, and study tools | Record focused work; paused time counts |
| **Friends/Ranks/Shop** | Social motivation and rewards connected to real study activity | Compare progress and spend earned currency |

### Docs are sources; Lessons are curated AI notes

Docs contains material the user uploaded. Lessons contains structured AI-generated study notes. To ground Chat in a source, mark passages in Docs or type `@` in Chat and attach the document. Docs search uses fast filename/content search; **Refresh smart search** is only for a missing or stale semantic index.

### Why sign-in uses a PIN and a workspace passkey

Each account has one encrypted workspace; users do not create or manage multiple vaults.

- The **sign-in PIN** verifies the account identity.
- The **workspace passkey** decrypts private Docs, chats, Lessons, and progress.
- The same passkey unlocks synced encrypted data on another device.

The credentials are separate so obtaining the account PIN is not enough to decrypt private study history. The onboarding screen explains this distinction when each credential is requested.

## Reliability cleanup in this release

- Ordinary Chat no longer downloads or executes the browser embedding model before sending a message.
- Docs filename/content search is debounced and visibly reports progress.
- Streaming answers render Markdown and batch updates to reduce UI work.
- Resource failures return a useful retry message instead of leaking `OSError`.
- Profile is surfaced in primary navigation and Dashboard.
- The Friends search clear control no longer shifts on hover.
- Pausing the focus timer saves the elapsed segment.
- Backend-facing **Rebuild index** terminology is replaced with **Refresh smart search**.
- The production React bundle and regression coverage are updated with the source.

## Repository layout

| Directory | Responsibility |
|---|---|
| `frontend/src/` | React, TypeScript, Tailwind, Zustand, streaming Markdown UI |
| `backend/app/` | FastAPI routes, services, storage, encryption, sync, and AI orchestration |
| `engine/` | Document pipeline, quiz engine, shared account artifacts, and CLI entrypoints |
| `backend/tests/` | API, service, storage, security, and browser-logic regression tests |
| `backend/app/templates/` | Production frontend bundle served by FastAPI |
| `api/` | Vercel serverless entrypoint (`from backend.app.main import app`) |
| `scripts/` | Development, sync, quiz, smoke, and CI audit utilities |
| `docs/` | Architecture, planning, testing, product, and review material |

### Repository tooling

| File | Purpose |
|---|---|
| [`pyproject.toml`](pyproject.toml) | pytest `pythonpath` and ruff configuration. Without the `pythonpath` entry a bare `pytest` cannot import `backend.app` or `engine`. |
| [`.python-version`](.python-version) | Pins Python 3.13, matching every CI workflow |
| [`.vercelignore`](.vercelignore) | Keeps docs, tests and frontend sources out of the deployed function. Must not gain a blanket `*.md` rule: `backend/prompts/*.md` are read at runtime. |
| [`license-policy.json`](license-policy.json) | Allowed licence list enforced by [`scripts/check_licenses.py`](scripts/check_licenses.py) |
| [`.env.example`](.env.example) | Every environment variable the code reads, with its default |

## What the system provides

- Document extraction, chunking, search, and optional semantic indexing.
- Streaming AI tutor with explicit document grounding and study tools.
- Background lesson generation with planner, bounded workers, reviewer, sources, and embedded quizzes.
- Quiz grading, XP, rank, leaderboard, Friends, Shop, and Dashboard progression.
- Workspace goals, calendar, code tools, and focus timer.
- PIN authentication plus passkey-derived encrypted account data.
- Local SQLite operation, PostgreSQL/Supabase hosting, and optional encrypted push/pull sync.

## Quick Start

1. Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
pip install --require-hashes -r engine\requirements.txt
```

If you change dependencies, update the lockfile with:

```powershell
python -m piptools compile --generate-hashes --strip-extras --no-annotate --allow-unsafe --resolver=backtracking --output-file engine/requirements.txt engine/requirements.in
```

Backend/frontend hash-locked dependency files:

- `backend/requirements.lock.in` -> `backend/requirements.lock.txt`
- `frontend/requirements.lock.in` -> `frontend/requirements.lock.txt`

Regenerate with:

```powershell
python -m piptools compile --generate-hashes --strip-extras --no-annotate --output-file backend/requirements.lock.txt backend/requirements.lock.in
python -m piptools compile --generate-hashes --strip-extras --no-annotate --output-file frontend/requirements.lock.txt frontend/requirements.lock.in
```

Security tooling lockfile (used by CI) can be regenerated with:

```powershell
python -m piptools compile --generate-hashes --strip-extras --no-annotate --allow-unsafe --resolver=backtracking --output-file engine/requirements-security.txt engine/requirements-security.in
```

3. Configure `.env` at repo root:

```env
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_MODEL=deepseek-v4-flash
UNLOCK_TOKEN_SECRET=your_secret_here
# Optional: use a different LLM provider (OpenAI, local, proxy)
# DEEPSEEK_BASE_URL=https://api.openai.com/v1
# Local SQLite mode (set this for local development):
# LOCAL_DATABASE_URL=sqlite:///backend/data/obital.db
# Cloud Postgres mode (set this for Vercel/hosting):
# DATABASE_URL=postgresql+psycopg://...@...supabase.com:6543/postgres
# SYNC_INTERVAL_MINUTES=30
```

> **Note**: Set `LOCAL_DATABASE_URL` for local SQLite mode, or `DATABASE_URL` for cloud Postgres mode. Both can be set simultaneously for SQLite-primary with cloud sync.

4. Run the pipeline (account-scoped):

```powershell
python -m engine.pipeline.main --account-id admin --run-qa
```

## Current Scope

- Offline CLI pipeline
- Runtime web API service in `backend/` with agent harness, tool calling, and async generation jobs
- React web application in `frontend/src/`, built into `backend/app/templates/` for production
- Secondary Textual desktop interface in `frontend/app.py`

## Backend Run (Cloud DB)

```powershell
.\scripts\dev\run_backend_cloud.ps1
```

This script loads `.env` into process env and uses `DATABASE_URL`.

## Backend Run (Local Mode)

```powershell
.\scripts\dev\run_backend.ps1
```

Local mode uses SQLite at `backend/data/obital.db` with full E2EE encryption. No database configuration required — just set your `DEEPSEEK_API_KEY` and `UNLOCK_TOKEN_SECRET` in `.env`.

## Web TUI

After starting the backend, open **http://localhost:8000** in your browser. In cloud/client embedding mode, the browser generates MiniLM vectors only for explicit document indexing. Ordinary Chat sends immediately and uses marked or `@`-attached passages for source grounding.

### Embedding Mode

Cloud deployments should use:

```env
EMBEDDING_MODE=client
EMBEDDING_ALLOW_HASH_FALLBACK=false
```

In this mode, server-side embedding endpoints are disabled unless the request supplies a browser-generated `query_vector`. This prevents production from silently falling back to non-semantic hash vectors. Existing documents with old vectors can be opened in Docs and updated with **Refresh smart search**.

Use `EMBEDDING_MODE=server` only for local deployments that intentionally install `sentence-transformers` and its PyTorch dependency.

## Frontend-Backend Connection

Obital has three connection paths between frontend and backend:

### 1. Web TUI (Production — same-origin)

The FastAPI backend serves the Web TUI at `GET /` from `backend/app/main.py:68-78`:

```python
@app.get("/", response_class=HTMLResponse)
def root() -> HTMLResponse:
    template_path = Path(__file__).parent / "templates" / "index.html"
    ...
```

The HTML/JS terminal at `backend/app/templates/index.html` makes all API calls with relative URLs (`BASE = ''`). In production (Vercel or local), everything is same-origin — no proxy needed.

### 2. React Dev Server (Development — Vite proxy)

When developing with hot-reload, the Vite dev server runs on `port 5173` and proxies API routes to the backend:

| Vite Proxy (`frontend/vite.config.ts:8-19`) | Target |
|---|---|
| `/auth`, `/chat`, `/docs`, `/lessons` | `http://localhost:8000` |
| `/quiz`, `/rewards`, `/settings`, `/sync` | `http://localhost:8000` |
| `/usage`, `/pipeline`, `/health` | `http://localhost:8000` |

Run both:
```powershell
# Terminal 1: backend
.\scripts\dev\run_backend.ps1

# Terminal 2: frontend dev server
cd frontend
npm run dev
```
Then open **http://localhost:5173**.

### 3. Textual TUI (Desktop — direct HTTP)

The Python Textual app connects directly to the backend via httpx:

```python
# frontend/app.py:862
base_url = os.environ.get("OBITAL_BACKEND_URL", "http://127.0.0.1:8000")
```

Start both together with:
```powershell
.\scripts\dev\run_obital.ps1
```

### Auth Headers

All frontends attach these headers to API requests (from `frontend/src/api/client.ts:14-15` and `frontend/services/api_client.py`):

| Header | Source | Purpose |
|---|---|---|
| `X-Unlock-Token` | Response from `POST /auth/unlock` | E2EE session auth |
| `X-Client-Id` | Stable client UUID (localStorage / in-memory) | Token binding |

### CORS

CORS defaults to `http://localhost:5173,http://localhost:3000`. Override via `CORS_ORIGINS` env var (comma-separated) or leave empty to disable CORS.

## Testing

Testing evidence is documented in [TESTING.md](docs/testing/TESTING.md), including named cases, source files, commands, verified counts, regression coverage, browser-test boundaries, and manual/user-testing records.

| Evidence group | Examples | Current maintained baseline |
|---|---|---:|
| Unit | Quiz grading/modes, checkpoint normalization, unlock validation, storage invariants | 44 passed |
| API/integration | Sync authorization/lockout, encrypted workspace round-trip, route validation/SSE, lesson materials | 40 passed |
| Browser runtime | Chromium grading/vector behavior and mocked pipeline orchestration | 40 passed |
| Manual system | Onboarding, deployment, Docs, Chat, Lessons/quizzes, Dashboard, timer, sync | Case log; retest pending |
| User testing | External MS3 exploratory session and remediation traceability | UT-01 documented |

Run the maintained automated evidence groups:

```powershell
# Unit tests (44 cases)
python -m pytest engine/tests/test_quiz_modes.py engine/tests/test_checkpoint_normalization.py backend/tests/test_auth_api.py backend/tests/test_stateless_storage.py -q

# API/integration tests (40 cases)
python -m pytest backend/tests/test_sync_api.py backend/tests/test_workspace_api.py backend/tests/test_stream_and_routes.py backend/tests/test_lesson_materials.py -q

# Real Chromium runtime (40 cases)
pip install -r backend/tests/requirements-test.in
python -m playwright install chromium
python -m pytest backend/tests/test_browser_js.py -q
```

The Chromium suite is real browser-runtime testing, but it is not presented as complete visual E2E coverage: 37 cases execute JavaScript grading/vector behavior in Chromium and 3 navigate the live FastAPI app while mocking pipeline endpoints. Real onboarding, hover, responsive, AI-provider, and cross-device behavior is covered by [manual system cases](docs/testing/MANUAL_SYSTEM_TESTS.md) and the [MS3 user-testing record](docs/testing/MS3_USER_TESTING.md).

Regression testing runs the maintained groups on each pull request through [`.github/workflows/quality-tests.yml`](.github/workflows/quality-tests.yml). Named regressions cover retryable Chat resource errors, unlock invalidation, encrypted-data preservation, stale document caches, invalid checkpoint indexes, and pipeline failure short-circuiting.

## Optional Cloud Sync

Cloud is opt-in. The Web TUI auto-pulls on login when cloud is connected. Manual sync is always available.

### User-facing: unlock then use F2/F3 in the Web TUI or F7/F8 in Textual

1. Start the backend: `.\scripts\dev\run_backend.ps1` (local) or `.\scripts\dev\run_backend_cloud.ps1` (cloud)
2. Start the frontend: `.\scripts\dev\run_frontend_textual.ps1` or open the browser TUI
3. Sign in and unlock the vault
4. Press `F2`/`F3` (Web) or `F7`/`F8` (Textual) for manual sync
5. The app calls `/auth/unlock` to obtain a short-lived token, then calls `/sync/push` or `/sync/pull`
6. The token is held in-memory only and never persisted to disk

### Auto-sync via background scheduler

Set `SYNC_INTERVAL_MINUTES` in `.env` to enable periodic background sync:

```env
DATABASE_URL=postgresql+psycopg://...@...supabase.com:6543/postgres
SYNC_INTERVAL_MINUTES=30
```

The scheduler syncs all accounts with active unlock sessions every N minutes. Set to `0` to disable.

### Sync safety

- **API push is additive** — documents uploaded via the Web TUI and artifacts from the local filesystem coexist safely. API sync never deletes cloud artifacts.
- **`scripts/sync/run_cloud_sync.ps1` is authoritative** — the local admin script passes `delete_stale=True` for full local→cloud sync including deletion of removed files.
- **Bin state syncs** — `bin_items` rows (soft-delete metadata, restore markers, TTL) are mirrored between local and cloud SQLite on every push/pull.
- **Deferred content hydration** — on login, `/auth/bootstrap` pulls metadata only (essentials mode). Document content is hydrated in the background. `GET /sync/status` returns per-account hydration counts. Document reads trigger on-demand hydration transparently.

### Admin/recovery: direct PowerShell scripts

```powershell
.\scripts\sync\run_cloud_sync.ps1 -AccountId admin -Passphrase "<account passphrase>"
.\scripts\sync\run_cloud_pull.ps1 -AccountId admin -Passphrase "<account passphrase>"
```

These scripts use the shared unlock guard (same lockout/failure tracking as the API) and are intended for operator recovery only.

## Data Direction (Important)

- `scripts/sync/run_cloud_sync.ps1` is an **account-scoped local -> cloud** sync (admin/recovery only).
- `scripts/sync/run_cloud_pull.ps1` is an **account-scoped cloud -> local** sync (admin/recovery only).
- Frontend F7/F8 are the **user-facing** sync paths — they call `/sync/push` and `/sync/pull` via the unlock token model.
- `scripts/dev/run_backend_cloud.ps1` uses cloud DB directly (your app reads/writes cloud data live).

## Encryption Unlock (DB Mode)

When backend runs in DB/cloud mode, unlock per account with:

- `POST /auth/unlock?account_id=<id>` body: `{ "passphrase": "..." }`
- Use returned `token` as `X-Unlock-Token` in encrypted requests
- Send a stable `X-Client-Id` header (min 8 chars) from the local app; unlock tokens are bound to that client context
- `POST /auth/lock?account_id=<id>` invalidates the current unlock token version for that account
- Invalid unlock tokens fail with `401` instead of falling through to unreadable ciphertext responses
- Cloud sync uses the same guard: `POST /sync/push` and `POST /sync/pull` require `X-Unlock-Token` + `X-Client-Id`

Required env for unlock tokens:

- `UNLOCK_TOKEN_SECRET`

Optional env tuning:

- `UNLOCK_TOKEN_TTL_MINUTES`
- `UNLOCK_MAX_FAILED_ATTEMPTS`
- `UNLOCK_LOCKOUT_MINUTES`

## Frontend Run (Textual)

```powershell
.\scripts\dev\run_frontend_textual.ps1
```

## Unified Startup (Backend + Frontend)

```powershell
.\scripts\dev\run_obital.ps1
```

## Agent Harness & Study Tools

The latest commit adds an **agent harness** that lets the LLM tutor autonomously invoke study tools during chat. Key components:

- **`agent_harness.py`** — Orchestrates LLM chat turns with tool-calling support. Parses native OpenAI `tool_calls` and a proprietary DSML XML-like format for models that can't emit structured calls. Limits to 5 tool calls per turn with a follow-up LLM summary. Mutation tools require user confirmation via pending action queue.
- **`study_tools.py`** — 14 tool definitions (read & mutation) covering system prompt management, dashboard summaries, lesson CRUD, document excerpts, QA generation jobs, Wikipedia search, quiz lifecycle, and answer submission.
- **`harness_state.py`** — Thread-safe JSON-file store for pending (unconfirmed) agent tool actions, keyed by `session_id` + `account_id`.
- **`generation_jobs.py`** — Lightweight background job system using daemon threads and JSON persistence. Creates async QA generation jobs with status polling (`queued → running → succeeded/failed`).
- **`api/pipeline.py`** — FastAPI endpoints for curriculum generation (`POST /pipeline/generate-curriculum`), topic QA generation (`POST /pipeline/generate-topic-qa`), save (`POST /pipeline/save`), and async job lifecycle (`POST /pipeline/jobs/qa-generation`, `GET /pipeline/jobs/{job_id}`).

When the LLM requests a mutation (e.g. creating a lesson, saving QA), the harness stores it as a pending action. The frontend prompts the user to confirm via `POST /chat/confirm-action`, and the tool only executes on approval.

## Vercel Web Deployment

Obital is fully configured for stateless, cost-efficient hosting on **Vercel Serverless Functions**. In this mode, the backend runs entirely stateless on Vercel's global edge network, saving cost by scaling down to zero when idle, while securely connecting to your PostgreSQL cloud database.

### Prerequisites
1. A **Vercel** account (free hobby tier is perfect).
2. A **PostgreSQL** cloud database (e.g., Supabase, Neon, or AWS RDS).
3. **DeepSeek API Key** for tutor and intelligence routing.

### Environment Variables
Configure the following environment variables in your **Vercel Project Dashboard** (Settings > Environment Variables):

| Variable Name | Description | Example / Recommended Value |
| :--- | :--- | :--- |
| `DATABASE_URL` | PostgreSQL connection string. | `postgresql://user:pass@ep-dummy.neon.tech/neondb?sslmode=require` |
| `DEEPSEEK_API_KEY` | Your DeepSeek API service token. | `sk-proj-...` |
| `DEEPSEEK_MODEL` | DeepSeek LLM intelligence model. | `deepseek-v4-flash` |
| `DEEPSEEK_BASE_URL` | LLM API base URL (optional). | `https://api.deepseek.com` |
| `UNLOCK_TOKEN_SECRET` | Secret key for E2EE unlock sessions. | A 32-character hexadecimal key (e.g. run `openssl rand -hex 16`) |
| `DB_AUTO_CREATE_SCHEMA` | Automatically create the DB tables on startup. | `True` |

### Step-by-Step Deployment

#### Deploying the React Web UI

Before pushing to Vercel, build the React frontend and copy it to the backend templates. This script also commits and pushes the built UI to git:

```powershell
.\scripts\dev\build_frontend.ps1
```

This runs `npm install && npm run build` in `frontend/`, copies `frontend/dist/` to `backend/app/templates/`, and commits + pushes the result. The backend serves the React build at `GET /` on Vercel.

#### Method A: Git Integration (Recommended)
1. **Build, commit, and push the React frontend**: `.\scripts\dev\build_frontend.ps1`
2. Go to your [Vercel Dashboard](https://vercel.com/dashboard) and click **Add New > Project**.
3. **Import** your repository.
4. Expand the **Environment Variables** section and inject the variables listed above.
5. Click **Deploy**. Vercel will automatically read `vercel.json` and the root `requirements.txt` to build and host the FastAPI server.

#### Method B: Vercel CLI
1. Build, commit, and push the React frontend: `.\scripts\dev\build_frontend.ps1`
2. Install the Vercel CLI locally:
   ```powershell
   npm install -g vercel
   ```
3. Run the deployment command from the repository root:
   ```powershell
   vercel
   ```
4. Set your project settings and environment variables in the interactive prompt or Vercel Web UI.

---

### E2EE & Cloud Security Architecture

### Current Vercel lesson-generation limitation

The current deployment uses `/tmp/obital/lesson_generation_jobs.json` for
lesson-job state because Vercel's `/var/task` filesystem is read-only. Lesson
generation is therefore intended to complete while the user keeps the chat
open. The temporary state may disappear on cold starts, is not shared across
serverless instances, and is limited by the Vercel function timeout. A
Postgres-backed queue and persistent worker are required for durable offline
processing.

* **100% E2EE Cloud Compliance**: All uploaded user documents, note chunks, user-tutor configuration settings (such as custom prompts), and sensitive progress tracking databases (quiz histories, lesson attempts, and curriculum checkpoint status) are securely encrypted in-memory under the user's active **Data Encryption Key (DEK)** before ever being sent to the database.
* **Encrypted/Metadata Split Registry**: User registry records dynamically split credentials from sensitive study history. Common metadata required for pre-unlock authentication (PIN validation hashes) remains plain-text to verify logins, while all private, user-originated curriculum histories are securely locked under E2EE base64 ciphertext.
* **Unlock Sequence**: The TUI and browser clients dynamically authenticate via `/auth/unlock`, deriving the DEK from the user's passphrase to populate a thread-safe server-side context and activate secure transparent decryption without exposing private keys.

---

## Web TUI (Browser Terminal)

The root route `/` serves a full-featured browser-based terminal. Available on Vercel or any backend instance.

### Layout

```
Lessons | Secure Terminal | Documentation
```

### Key Bindings

| Key | Action |
|-----|--------|
| `F1` | Help |
| `F2` | Sync Push (local → cloud) |
| `F3` | Sync Pull (cloud → local) |
| `F4` | Upload Document |
| `F5` | Settings (system prompt) |
| `F6` | Dashboard (points, quiz trend graph, logs) |
| `ESC` | Close modal / Reset chat session |
| `Ctrl+L` | Clear terminal |
| `Ctrl+X` | Lock vault |
| `Ctrl+C` | Cancel active stream / Clear input |
| `Ctrl+F` | Focus reader search (when reader is open) |
| `↑` / `↓` | Command history |
| `Tab` | Autocomplete commands |
| `back` | Go back at any auth step |

### Features

- **SSE Streaming** — Chat responses stream token-by-token; Ctrl+C cancels mid-stream
- **Deployment-aware search** — fast filename/content search works everywhere; optional semantic indexing runs in the configured client or server mode
- **Inline MCQ Quiz** (`/quiz` command) — interactive multiple-choice questions rendered in the terminal with click-to-select, arrow key navigation, and LaTeX/Markdown rendering
- **Settings Modal** — edit the AI tutor system prompt inline
- **Dashboard** — SVG bar chart of quiz score trend, summary stats, session logs
- **Document Reader** — full-screen modal with chunk selection checkboxes and search for tutor context
- **Cloud Status** — shows "Cloud: connected" or "Mode: local storage" after login; connection changes shown as toast notifications
- **Auto-pull on login** — pulls latest cloud data when connected; manual F2/F3 also available
- **Terminal footer** — live message count, sync timestamp, lock status, backend health
- **Command autocomplete** — Tab fills in matching commands, ↑/↓ cycles through history
- **Toast notifications** — sync success/failure and connection changes shown as slide-in toasts
- **Auth back navigation** — type `back` at any auth step to return to the previous screen

### Commands

```
/help          Show grouped command reference with keyboard shortcuts
/quiz          Start an MCQ quiz (supports topic=, mode=, count=, difficulty=)
/lessons       List lessons with checkpoint progress
/lessons start <topic>  Start a new lesson on a topic
/lessons open <index>   Select a lesson (binds to chat context)
/lessons restart <N>    Restart a lesson from checkpoint 1
/lessons continue       Continue an in-progress lesson
/lessons clear          Unbind active lesson
/lesson quiz            Run the next checkpoint quiz
/lesson answer A,B,C    Submit quiz answers for grading
/lesson status          Show active lesson progress
/lesson continue        Continue an in-progress lesson
/docs          List and open documents (with chunk selection and search)
/upload        Upload a file
/pipeline      Generate curriculum and QA sets from uploaded documents
/sync push     Push to cloud
/sync pull     Pull from cloud
/sync status   Show sync and hydration status
/bin           List trashed items with days remaining
/bin restore <id>  Restore an item from the bin
/reset         Reset chat session
/settings      Open settings editor (system prompt)
/dashboard     Open rewards dashboard
/clear         Clear terminal
/lock          Lock vault and sign out
<any text>     Chat with the AI tutor (streams token-by-token)
```
