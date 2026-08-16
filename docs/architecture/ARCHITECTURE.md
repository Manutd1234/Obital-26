# Obital — System Architecture

## Overview

Obital is a personal, offline-first AI study companion with optional cloud sync. It ingests educational documents, builds RAG search indexes, generates curriculum and Q&A artifacts via an LLM, and provides an interactive quiz chatbot with account persistence, lesson progression, and a points/rewards system.

```
                    ┌────────────────────────────────────────┐
                    │            User Interfaces              │
                    │  ┌──────────┐  ┌────────────────────┐  │
                    │  │ Textual  │  │  Web TUI (Vercel)  │  │
                    │  │ TUI App  │  │  HTML/JS Terminal   │  │
                    │  └────┬─────┘  └─────────┬──────────┘  │
                    └───────┼──────────────────┼─────────────┘
                            │                  │
                    ┌───────┼──────────────────┼─────────────┐
                    │       ▼                  ▼              │
                    │  ┌──────────────────────────────┐      │
                    │  │     FastAPI Backend Layer     │      │
                    │  │                              │      │
                    │  │  /auth   /sync   /chat       │      │
                    │  │  /docs   /lessons /settings   │      │
                    │  │  /health /rewards             │      │
                    │  └──────────┬───────────────────┘      │
                    │             │                          │
                    │    ┌────────┴────────┐                 │
                    │    ▼                  ▼                 │
                    │ ┌──────────┐  ┌──────────────┐        │
                    │ │  Local   │  │   Cloud DB    │        │
                    │ │  JSON    │  │  (PostgreSQL) │        │
                    │ │  Files   │  │               │        │
                    │ └──────────┘  └──────────────┘        │
                    └───────────────────────────────────────┘
                                     │
                    ┌────────────────┼───────────────────────┐
                    │                ▼                        │
                    │  ┌──────────────────────────────┐      │
                    │  │          Engine Core         │      │
                    │  │                              │      │
                    │  │  Document Ingestion           │      │
                    │  │  Text Extraction (PDF/TXT/MD) │      │
                    │  │  RAG Index Build (LightRAG)   │      │
                    │  │  Curriculum + QA Generation   │      │
                    │  │  Quiz Chatbot Engine          │      │
                    │  └──────────────────────────────┘      │
                    └───────────────────────────────────────┘
```

---

## Project Layout

```
Obital/
├── engine/                       # Core offline pipeline + quiz engine
│   ├── pipeline/                 # Doc ingestion, RAG, curriculum/QA generation
│   ├── quiz/                     # Quiz backend, account system, chatbot CLI
│   ├── shared/                   # Account path resolver, context builder, embeddings
│   ├── tests/                    # Engine test suite
│   ├── docs/                     # Pipeline + quiz architecture docs
│   ├── accounts/                 # Per-account runtime artifacts (gitignored)
│   └── requirements*.txt         # Hash-locked pipeline + security tooling sets
│
├── backend/                      # FastAPI web service
│   ├── app/
│   │   ├── api/                  # REST endpoints (auth, sync, chat, docs, quiz, pipeline, ...)
│   │   ├── services/             # Business logic, crypto, DB, sync, storage, embeddings
│   │   ├── templates/            # Production React bundle served by FastAPI
│   │   ├── main.py               # App factory, static mount, SPA root route
│   │   └── schemas.py            # Pydantic models
│   ├── prompts/                  # Runtime-loaded system prompts (*.md, required in deploy)
│   ├── scripts/                  # Admin migration/backfill CLI scripts
│   ├── alembic/                  # DB schema migrations
│   ├── tests/                    # Backend test suite
│   └── data/                     # Local runtime data (SQLite DB, chat, logs; gitignored)
│
├── frontend/                     # Two clients share this directory
│   ├── src/                      # React + TypeScript web app (the deployed UI)
│   ├── app.py, ui/               # Textual TUI desktop client
│   ├── services/                 # API client and engine CLI bridge (TUI)
│   └── tests/                    # Frontend test suite
│
├── api/                          # Vercel serverless entrypoint
│   └── index.py                  # imports backend.app.main:app
│
├── .github/workflows/            # Five CI workflows (quality, licence, security x2, attribution)
├── docs/                         # Architecture, planning, testing, product, review material
├── scripts/                      # Development, sync, quiz, smoke, and CI audit utilities
├── README.md                     # Project overview + quick start
├── CHANGELOG.md                  # Detailed change history
├── SECURITY.md                   # Security policy and threat model
├── pyproject.toml                # pytest + ruff configuration
├── .python-version               # Pinned interpreter (3.13), matches CI
├── vercel.json / .vercelignore   # Deployment config and upload exclusions
└── .env.example                  # Every environment variable, with defaults
```

---

## Three-Layer Architecture

### Layer 1: Engine (Core Pipeline)

The `engine/` package is the offline document processing and quiz engine. It has no web dependencies.

**Pipeline** (`engine/pipeline/`):
- **Input Discovery**: Recursively walks `accounts/{id}/documents/` for `.pdf`, `.txt`, `.md` files.
- **Text Extraction**: `extract_text.py` normalizes raw text; `pypdf` for PDFs.
- **RAG Index**: `rag_engine.py` chunks text, computes embeddings, and builds a LightRAG graph index. Uses a cache manifest to skip unchanged files.
- **Curriculum Synthesis**: `qa_pipeline.py` calls DeepSeek LLM to generate a topic curriculum from the ingested knowledge graph.
- **QA Generation**: Per-topic concurrent QA set creation (quiz questions, answers, explanations).
- **Merge Output**: Writes merged `output_curriculum_qa.json` into the account directory.

**Quiz** (`engine/quiz/`):
- **Quiz Backend** (`quiz_backend.py`): Builds quiz sessions from curriculum QA, grades responses with typo-tolerant matching.
- **Account System** (`account_system.py`): `AccountStore` — manages users, PIN auth (PBKDF2-HMAC-SHA256), quiz history, lesson progression, and points/rewards. Stores in local JSON and/or cloud PostgreSQL transparently via the `_is_stateless_db_mode()` switch.
- **Chatbot CLI** (`quiz_chatbot_cli.py`): Interactive terminal quiz interface with lesson mode, topic filtering, and difficulty selection.
- **Lesson Progression**: Checkpoint-based system — each lesson has milestones with pass thresholds. Advancing unlocks the next checkpoint.

> **Deprecated**: The CLI lesson commands (`_start_lesson_from_prompt`, `_run_active_lesson_checkpoint`, `_handle_lesson_command`) and `build_checkpoint_quiz` are deprecated. Use the lesson harness (backend/app/services/lesson_harness.py) for new lessons with embedded quizzes.

For detailed internals, see:
- `engine/docs/pipeline/ARCHITECTURE.md`
- `engine/docs/quiz/QUIZ_BACKEND_ARCHITECTURE.md`

### Layer 2: FastAPI Backend

The `backend/` package adapts the engine pipeline into REST APIs. It runs either locally (JSON file storage) or in cloud mode (PostgreSQL).

**API Endpoints**:

| Prefix | Purpose | Auth |
|--------|---------|------|
| `/auth` | Register, login (PIN), unlock/lock (passphrase), **bootstrap** (unlock + data in one call) | Public |
| `/sync` | Push/pull account data between local ↔ cloud | `X-Unlock-Token` |
| `/chat` | Tutor chat sessions and messages (`POST /messages` for JSON, `POST /messages/stream` for SSE) | `X-Unlock-Token` (optional) |
| `/docs` | Document upload, list, search (keyword + vector + tag-filtered) | `X-Unlock-Token` (optional) |
| `/lessons` | List lessons, start/attempt/restart/grade lessons, resolve by query, generate via harness | Public |
| `/settings` | Get/patch system prompt and config | `X-Unlock-Token` (optional) |
| `/rewards` | Dashboard, session logs | Public |
| `/quiz` | Build quiz from QA artifacts, **grade quiz answers server-side** | `X-Unlock-Token` (optional) |
| `/pipeline` | Curriculum/QA generation, artifact save | `X-Unlock-Token` (optional) |
| `/bin` | List, restore, permanently delete trashed items | `X-Unlock-Token` (optional) |
| `/health` | Health check and DB connectivity | Public |
| `/usage` | Token/doc/lesson usage tracking and limits | `X-Unlock-Token` (optional) |

**Dual Storage Mode**:

The backend switches storage backends based on environment variables:

```
LOCAL_DATABASE_URL set (sqlite:///...)  →  SQLite mode (local)
  - Accounts: account_registry_records table
  - Chat: chat_sessions + chat_messages tables
  - Docs: account_artifacts table
  - Rewards: reward_sessions + reward_events tables
  - Data stored as plaintext (no per-write encryption)
  - Encryption only happens during cloud sync

DATABASE_URL set (postgresql://...)  →  stateless DB mode (Postgres)
  - Same tables, stored in cloud PostgreSQL
  - All sensitive fields E2EE encrypted at rest
  - Cloud sync not needed (data is already in cloud)

Both set  →  SQLite primary + Postgres cloud sync target
  - Local reads/writes go to SQLite
  - Periodic sync pushes encrypted data to Postgres
```

**E2EE Encryption Architecture**:

```
User Passphrase (entered at unlock)
        │
        ▼
  Argon2id key derivation
        │
        ▼
   KEK (Key Encryption Key)
        │
        ├── wraps ──► DEK (Data Encryption Key) ── stored in account_keys
        │                                            as wrapped_dek_b64
        │
        ▼
   DEK ── encrypts ──► chat message text
                   ──► reward event notes
                   ──► quiz_history (account registry)
                   ──► lesson_progress (account registry)
                   ──► uploaded documents (account_artifacts)
                   ──► document embeddings (account_artifacts, .embeddings.json)
                   ──► QA artifacts (account_artifacts, output_curriculum_qa.json)
                   ──► auto-generated QA (persisted to DB in stateless mode)
                   ──► system prompt / settings (config artifacts)
```

- **Unlock flow**: `POST /auth/unlock` → derive KEK from passphrase → unwrap DEK → create Fernet-encrypted session token
- **Token binding**: Tokens are bound to `X-Client-Id` header + per-account `unlock_version`
- **Lockout**: 5 failed attempts → temporary lockout with exponential backoff (doubles each lockout, max 240 min)
- **Auto-seeding**: On first load in DB mode, local `accounts.json` users are automatically seeded into the cloud DB (if the file exists in the deployment)

### Pipeline API (`/pipeline`)

The pipeline API enables QA generation directly from the web app, without requiring the local CLI pipeline:

```
POST /pipeline/generate-curriculum
  Input: { global_context: string, max_topics: int }
  Output: Curriculum (title, description, topics)
  Calls: DeepSeek LLM via CurriculumQAPipeline

POST /pipeline/generate-topic-qa
  Input: { topic_id, topic_title, topic_summary, context_chunks[], qa_per_topic }
  Output: TopicQASet (questions, answers, difficulty)
  Calls: DeepSeek LLM via CurriculumQAPipeline

POST /pipeline/save
  Input: { curriculum_qa: CurriculumQAOutput }
  Saves: output_curriculum_qa.json to DB or local disk
```

The browser orchestrates the full flow:
1. Call `/pipeline/generate-curriculum` with all chunk texts
2. For each topic, call `/pipeline/generate-topic-qa` with context chunks
3. Merge and save via `/pipeline/save`
4. Quiz is now available via `/quiz/build`

### Bootstrap API (`/auth/bootstrap`)

Single endpoint for post-login setup, replacing the previous multi-step unlock → health check → sync → fetch chain:

```
POST /auth/bootstrap?account_id={id}
  Input: { passphrase: string }
  Output: { token, is_cloud, sync, lessons[], docs[] }
  Steps:
    1. Unlock account (derive KEK → unwrap DEK → create session token)
    2. Sync pull from cloud (best-effort, doesn't block on failure)
    3. Fetch lessons and docs
    4. Return everything in one response
```

### Deployment-Aware Embedding Architecture

Obital supports client and server embedding modes while preserving a fast non-semantic path.

**Client mode** (`EMBEDDING_MODE=client`):
- Explicit document indexing runs in `frontend/src/workers/embedding.worker.ts`.
- Transformers.js uses WebGPU when available and WASM as a fallback.
- Generated vectors are saved through `PUT /docs/{id}/embeddings`.
- Ordinary Chat does not start the browser model; users ground a turn by marking passages or attaching a document with `@`.

**Server mode** (`EMBEDDING_MODE=server`):
- `services/embeddings.py` loads `all-MiniLM-L6-v2` through `sentence-transformers`.
- Upload and text-query endpoints may generate vectors server-side.
- Server mode is intended for installations that deliberately accept the PyTorch memory and deployment cost.

**Baseline search:**
- `GET /docs/search` searches document names and extracted text without loading an embedding model.
- Upload remains successful even when semantic enrichment fails.
- Hash-vector fallback is controlled by configuration and should not silently replace semantic vectors in production.

**Ordinary chat:**
```
User sends message
  → POST /chat/sessions/{id}/messages/stream
  → resolve marked or @-attached passages, if any
  → run fast-path study commands or LLM agent
  → stream Markdown tokens and structured progress events
```

**Document upload and explicit smart indexing:**
```
POST /docs/upload
  → validate, extract, chunk, and persist source
  → client or server generates optional vectors in configured mode
  → PUT /docs/{id}/embeddings persists vector metadata
  → source remains readable and text-searchable if indexing fails
```

**Quiz grading:**
```
POST /quiz/grade
  Input: { quiz: QuizBuildResponse, answers: [{question_id, answer}] }
  Output: QuizGradeResponse (per-question scores, correct/incorrect)
  Uses: engine.quiz.quiz_backend.grade_quiz()
  Semantic similarity via sentence-transformers (server-side)
```

---

## Tagging System

Documents and lessons carry optional `tags: list[str]` metadata enabling hybrid (semantic + tag-filtered) search.

### Data Model

| Object | Field | Source |
|--------|-------|--------|
| `Doc.tags` | Document-level tags | Written by LLM auto-tagger on upload |
| `DocChunk.tags` | Per-chunk tags | Inherited from doc-level tags |
| `Lesson.tags` | Topic-based tags | Derived from topic + part titles on read |

Tags are written into the `.embeddings.json` payload alongside chunk vectors:

```json
{"tags": ["python", "variables"],
 "chunks": [{"chunk_id": "c1", "text": "...", "vector": [...], "tags": ["python"]}]}
```

### Auto-Tagging Pipeline

- **Document upload**: `api/doc.py:_extract_doc_tags()` calls `deepseek-chat` with first 2000 chars of doc text, extracts 3-5 lowercase keyword tags. Runs once per upload before background embedding generation.
- **Lesson generation**: `lesson_harness.py:_derive_lesson_tags()` extracts tags from topic + part titles using simple tokenization and stopword filtering. Runs on every `_lesson_from_account_item()` read.
- **Backfill**: `backend/scripts/backfill_tags.py` scans existing embeddings across all accounts and tags any doc missing them.
- **Pipeline migration**: `backend/scripts/migrate_jsonl_to_docs.py` converts shared `vector_index.jsonl` rows into per-doc `.embeddings.json` files with tags.

### Search Filtering

Both `search_docs()` and `search_chunks_by_vector()` accept `filter_tags: list[str] | None`. When provided, only documents whose tags intersect the filter set are considered (OR semantics). The filter runs as a pre-filter — documents without matching tags are excluded before the keyword/vector search.

API endpoints exposing tag filtering:

| Endpoint | Tag Parameter |
|----------|---------------|
| `GET /docs/search?q=...&tags=python,variables` | Query param |
| `POST /docs/search { filter_tags: [...] }` | Request body |
| `POST /docs/search/text { filter_tags: [...] }` | Request body |

### Backward Compatibility

All `tags` fields default to `[]`. Existing `.embeddings.json` files without a `"tags"` key show empty tags — no data migration required, no breaking changes.

**Text search:**
```
POST /docs/search/text
  Input: { query: string, top_k: int, threshold: float, filter_tags?: string[] }
  → Server embeds query text via embeddings.embed_text()
  → Filters docs by tags if filter_tags provided (OR match)
  → Backend computes cosine similarity against stored chunk vectors
  → Returns top-K matching chunks with scores

GET /docs/search?q=...&tags=python,variables
  → Keyword search over doc names and chunk text, filtered by tags
```

**Key Services**:

| Module | Purpose |
|--------|---------|
| `services/db.py` | DB engine factory (SQLite/Postgres), session management, healthcheck, `is_stateless_db_mode()`, `is_sqlite_mode()`, `is_db_mode()`, cloud DB engine |
| `services/models.py` | SQLAlchemy models for all tables |
| `services/crypto_local.py` | Argon2id KEK derivation, AES-GCM encryption/decryption |
| `services/unlock_guard.py` | Passphrase validation, lockout tracking, account existence |
| `services/unlock_session.py` | Fernet-encrypted token creation, DEK resolution, `resolve_dek_for_account()` for background sync |
| `services/account_sync.py` | Push/pull account data between local SQLite and cloud DB (handles all encryption) |
| `services/sync_scheduler.py` | Background sync scheduler for periodic cloud push/pull |
| `services/chat_service.py` | Chat session/message CRUD, token usage recording |
| `services/chat_engine.py` | LLM tutor reply generation with RAG search, Wikipedia fallback, tool calling, DSML tag handling, thinking mode, dynamic tool spec |
| `services/chat_tools.py` | Fast-path intent router, lesson/quiz orchestration, auto-QA generation with DB persistence |
| `services/storage.py` | Document, lesson, rewards CRUD; embedding save/attach with DB/file fallback; **tag-based document/chunk metadata**; bin CRUD (delete/restore/list/permanent-delete) |
| `services/bin_cleanup.py` | Background daemon that permanently deletes expired bin items hourly |
| `services/settings_service.py` | System prompt and config CRUD |
| `services/embeddings.py` | Optional server embedding service and configured fallback controls |
| `services/usage.py` | Usage tracking, limit enforcement, periodic token reset |

### Layer 3: Frontend Interfaces

**Textual TUI** (`frontend/`):

A Python desktop app using the Textual framework. Three-pane layout:

```
┌─────────────┬──────────────────────────────┬─────────────┐
│   LESSONS   │       CHAT / QUIZ            │   DOCS      │
│             │                              │             │
│ [lesson]    │  > help                      │ [doc.pdf]   │
│ [lesson] *  │  Obital AI: ...              │   [chunk]   │
│             │                              │   [chunk]   │
│             │  >                            │             │
└─────────────┴──────────────────────────────┴─────────────┘
│  F6:Help  F7:Sync Push  F8:Sync Pull  Settings  Dashboard │
```

- Bridges to the engine CLI subprocess for quiz functionality.
- **Optimized Bridge:** The CLI bridge (`engine_cli_bridge.py`) blocks on stdout reads when idle (reducing CPU wakeups to 0) and processes complete lines instantly without 0.2s idle-loop delays.
- **Input protection:** Temporarily disables the chat input box during active API calls to avoid double-submit race conditions.
- **Direct lesson commands:** After sign-in, lesson commands (`/lesson start`, `/lesson quiz`, `/lesson answer`, `/lessons`, etc.) route directly through the FastAPI REST client — no CLI bridge needed. Quiz build/grade uses `POST /quiz/build` and `POST /quiz/grade`; lesson attempts use `POST /lessons/{id}/attempt`.
- Separate Settings modal (Ctrl+S) and Rewards Dashboard modal (Ctrl+D)
- Cloud sync via F7/F8 → unlock modal → `/sync/push` or `/sync/pull`

**React web application** (`frontend/src`, built into `backend/app/templates`):

The primary product UI is a React/TypeScript single-page application served at `GET /` in production. It uses relative API URLs for same-origin deployment; the Vite development server proxies those routes to FastAPI. Primary surfaces include Chat, Dashboard/Profile, Lessons, Docs, Workspace, Shop, Ranks, Friends, Settings, and Bin.

Key client-side capabilities:
- **SSE streaming:** Chat messages stream token-by-token via `POST /chat/sessions/{id}/messages/stream` (SSE). Frontend reads via `ReadableStream` and appends tokens incrementally. Ctrl+C aborts mid-stream.
- **Streaming Markdown:** Tokens are batched and rendered through React Markdown, remark-math, and KaTeX during the response.
- **Thinking mode:** When `DEEPSEEK_THINKING_ENABLED=true`, the LLM outputs chain-of-thought reasoning before the final answer. Reasoning content is streamed as `"thinking"` SSE events and can be displayed in a collapsible section.
- **Post-login bootstrap:** Single `POST /auth/bootstrap` call handles unlock + sync + data fetch in one request.
- **Lesson quiz integration:** `/lesson start`, `/lesson quiz`, `/lesson continue` commands manage lesson lifecycle. Lessons can be generated via the lesson harness (`POST /lessons/generate`) which creates rich markdown content with embedded quizzes via LLM. Lesson quiz checks for embedded quiz data first, falling back to QA pool for old-style lessons. No manual pipeline step required.
- **Lesson harness:** `POST /lessons/generate` starts an async job that generates a 3-part lesson (foundations, application, mastery) with embedded quizzes. The generated quizzes are graded via `POST /lessons/{id}/checkpoints/{index}/grade` which uses the engine grading pipeline.
- **Quiz auto-generation:** Both `/lesson quiz` and `/quiz <topic>` auto-generate QA from uploaded documents when no QA artifact exists. The generated artifact is persisted to DB in stateless mode, ensuring subsequent API requests work without re-generation.

Heavy work is isolated from the main UI thread:
- AI orchestration, quiz grading, storage, encryption, and lesson jobs run server-side.
- Client-mode document indexing runs in a Web Worker.
- Ordinary Chat avoids client embedding initialization.

The generated production entry point is `backend/app/templates/index.html`; source changes belong in `frontend/src` and must be rebuilt rather than edited in the generated bundle.

---

## Data Flow: Sync Architecture

```
                 LOCAL MACHINE                        CLOUD
                 ─────────────                        ─────

  ┌──────────────────────┐         push_account_to_cloud()
  │ SQLite DB             │ ──────────────────────────────────►
  │ (backend/data/        │         (read local → encrypt →
  │  obital.db)           │          write to cloud DB)
  │                       │
  │ OR: Postgres          │         pull_account_from_cloud()
  │ (Supabase)            │ ◄──────────────────────────────────
  └──────────────────────┘         (read cloud → decrypt →
                                    write to local DB)
           │
           │  Background scheduler (SYNC_INTERVAL_MINUTES)
           └── auto-syncs every N minutes when configured
```

**Sync modes**:

- **SQLite → Postgres**: Local SQLite as primary, Supabase as cloud backup. `DATABASE_URL` is the sync target.
- **Postgres → Postgres**: Single Postgres instance, no sync needed (data is already in the cloud).
- **Background sync**: `SYNC_INTERVAL_MINUTES` env var enables periodic auto-sync (default: 0 = disabled).
- **Manual sync**: `POST /sync/push` and `POST /sync/pull` with `X-Unlock-Token` header.

**Sync safety rules**:
- The Web TUI API path (`POST /sync/push`) uses `delete_stale=False` — it never deletes cloud artifacts, preventing data loss when docs are uploaded directly to the cloud
- The admin recovery script (`scripts/sync/run_cloud_sync.ps1`) passes `delete_stale=True` for full authoritative sync from the local filesystem
- Pull gracefully skips artifacts it cannot decrypt (handles legacy/corrupted data)

---

## Authentication Layers

### Layer 1: PIN Authentication
- PBKDF2-HMAC-SHA256 with per-user random salt (200,000 iterations)
- Used for login identity verification in both local and cloud modes
- `POST /auth/register` → create user
- `POST /auth/login` → verify PIN → return success/failure

### Layer 2: Passphrase Unlock (E2EE)
- Argon2id key derivation from user passphrase
- Unlocks the DEK for E2EE encryption/decryption
- `POST /auth/unlock` → derive KEK, unwrap DEK, create session token
- `POST /auth/lock` → invalidate session, increment unlock version
- Token required for sync, encrypted reads, and document upload

---

## Database Schema

| Table | Primary Key | Purpose |
|-------|------------|---------|
| `account_registry_records` | `account_id` | Account profiles (PIN hash, quiz_history, lesson_progress) |
| `account_keys` | `account_id` | Encryption key material (salt, argon2 params, wrapped DEK, lockout state) |
| `account_unlock_sessions` | `token_hash` | Temporary unlock tokens (Fernet-encrypted DEK, 30-min TTL) |
| `account_artifacts` | `(account_id, relative_path)` | Encrypted account files (documents, config, output) |
| `chat_sessions` | `id` | Chat session metadata |
| `chat_messages` | `id` (auto) | Encrypted chat messages with `(session_id, seq_no)` uniqueness |
| `reward_sessions` | `session_id` | Reward/log session metadata |
| `reward_events` | `id` (auto) | Encrypted reward event snapshots |
| `bin_items` | `id` | Soft-deleted items with 30-day TTL (`item_type`: document/lesson, `expires_at` for auto-cleanup) |

Usage data is stored in `account_registry_records.profile_json` as a nested `"usage"` dict:

```json
{
  "usage": {
    "token_used": 0,
    "usage_reset_at": "2026-06-06T12:00:00+00:00",
    "doc_count": 0,
    "lesson_count": 0,
    "storage_bytes": 0
  }
}
```

---

## Deployment: Vercel

The entire backend runs as a single serverless function on Vercel:

```
vercel.json
    │
    ▼
All requests  →  api/index.py  →  backend.app.main:app (FastAPI)
                                       │
                                       ▼
                              PostgreSQL (Supabase/Neon/RDS)
```

**Key Vercel env vars**: `DATABASE_URL`, `DEEPSEEK_API_KEY`, `UNLOCK_TOKEN_SECRET`, `DB_AUTO_CREATE_SCHEMA=true`

**Key local env vars** (set in `.env`):

| Variable | Description | Default |
|----------|-------------|---------|
| `LOCAL_DATABASE_URL` | Local SQLite database path | Empty (disabled) |
| `DATABASE_URL` | Cloud Postgres database (for Vercel/hosting and sync target) | Empty (disabled) |
| `SYNC_INTERVAL_MINUTES` | Auto-sync interval (0 = disabled) | `0` |
| `DEEPSEEK_API_KEY` | LLM API key for tutor | Required |
| `DEEPSEEK_MODEL` | LLM model name | `deepseek-v4-flash` |
| `DEEPSEEK_BASE_URL` | LLM API base URL | `https://api.deepseek.com` |
| `DEEPSEEK_THINKING_ENABLED` | Enable thinking mode (chain-of-thought) | `false` |
| `DEEPSEEK_REASONING_EFFORT` | Thinking effort level (`low`/`medium` mapped to `high`) | `high` |
| `UNLOCK_TOKEN_SECRET` | Secret for E2EE unlock sessions | Required |
| `DB_AUTO_CREATE_SCHEMA` | Auto-create tables on startup | `true` |

**Startup flow**:
The FastAPI backend uses the modern `lifespan` context manager to orchestrate startup and shutdown lifecycles:
1. `load_dotenv()` — loads `.env` file (DATABASE_URL, API keys, etc.)
2. `init_db()` — auto-creates tables if `DB_AUTO_CREATE_SCHEMA=true`
3. `cleanup_expired_unlock_sessions()` — removes expired tokens from DB
4. `start_scheduler()` — starts background sync thread if `SYNC_INTERVAL_MINUTES > 0`
5. Embedding model preload — loads `all-MiniLM-L6-v2` in background thread
6. On first account access: seeds local `accounts.json` users into the DB
7. On shutdown: `stop_scheduler()` → `dispose_db_engines()` — stops sync, closes connections

**Database & Query Performance**:
To resolve N+1 ORM query overhead, the application uses SQLAlchemy eager loading (`selectinload`) for pulling chat message sequences (`ChatSessionModel.messages`) and reward logs/logs events (`RewardSessionModel.events`). This collapses database roundtrips for session syncs and dashboard logs from $O(N)$ down to single batch fetches, optimizing response times in both local and cloud environments.

---

## Security Model

| Concern | Approach |
|---------|----------|
| Data at rest | AES-GCM encryption with per-account DEK; all user content encrypted before DB write |
| Key derivation | Argon2id (KEK from passphrase) + AES-GCM (DEK wrapping) |
| Token security | Fernet (PBKDF2 + AES-CBC) with per-request client binding |
| Brute force | 5 failed unlocks → exponential lockout (doubles to 240 min max) |
| Account IDs | Regex validated `^[a-zA-Z0-9_-]{3,64}$` before normalization |
| PIN hashing | PBKDF2-HMAC-SHA256, 200k iterations, per-user random salt |
| Multi-tenancy | All queries filtered by `account_id`; session tokens bound to account + unlock version |
| Old-style artifacts | DB artifacts from a different encryption scheme are safely skipped during sync pull |

---

## Document References

| Document | Covers |
|----------|--------|
| `README.md` | Project overview, quick start, deployment |
| `docs/architecture/ARCHITECTURE.md` | This document — system design |
| `docs/architecture/DATA_PROCESSING_FLOW.md` | Upload, retrieval, chat, lesson, quiz, reward, and sync data flow |
| `docs/planning/PLAN.md` | Final-phase priorities, compatibility matrix, and release gates |
| `docs/planning/PRD.md` | Product goals, journeys, and acceptance requirements |
| `docs/planning/TECH_STACK.md` | Technology decisions and runtime boundaries |
| `docs/planning/workflow.md` | User journeys, recovery behavior, and QA workflow |
| `CHANGELOG.md` | Detailed change history by date |
| `SECURITY.md` | Security policy, threat model, vulnerability reporting |
| `backend/README.md` | API reference, encryption, migrations |
| `backend/tests/README.md` | Browser automation tests and test architecture |
| `engine/README.md` | Pipeline + quiz CLI usage |
| `engine/docs/pipeline/ARCHITECTURE.md` | Pipeline internals |
| `engine/docs/quiz/QUIZ_BACKEND_ARCHITECTURE.md` | Quiz subsystem design |
