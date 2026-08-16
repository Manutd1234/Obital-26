# Chat Services Documentation

Architecture overview for the chat subsystem in `backend/app/services/`.

---

## File Overview

| File | Role | Lines |
|------|------|-------|
| `chat_tools.py` | Fast-path intent router (slash commands, pattern matching, lesson/quiz orchestration, auto-QA with DB persistence, background job streaming) | ~1242 |
| `chat_service.py` | Service layer — message persistence, session validation, orchestration, SSE lesson job streaming, tool confirmation | ~493 |
| `chat_engine.py` | LLM integration — RAG search, Wikipedia fallback, tool calling, streaming, DSML handling, thinking mode, agent harness | ~928 |
| `storage.py` | Data access — sessions, docs, lessons, rewards (filesystem + DB) | ~1400 |
| `usage.py` | Usage tracking — token/doc/lesson limits, periodic reset | ~113 |
| `lesson_harness.py` | LLM lesson generation engine — async pipeline, planner/worker/reviewer stages, quiz normalization | ~1668 |
| `agent_harness.py` | DSML tool call parsing, multi-round agent loop, search budget tracking | ~509 |
| `study_tools.py` | Agent tool registry — create lesson, web search, QA generation, quiz tools | ~359 |
| `llm_json.py` | Async JSON LLM caller with tool support and DeepSeek thinking mode | ~260 |
| `web_search.py` | Exa + Wikipedia hybrid search with rate limiting | ~181 |
| `background_jobs.py` | Thread-safe JSON-backed job store with subtask tracking | ~165 |
| `generation_jobs.py` | Background QA generation job runner | ~92 |
| `harness_state.py` | Pending agent tool action store (mutation confirmation) | ~74 |

---

## Request Flow

### Non-Streaming (`POST /chat/sessions/{id}/messages`)

```
User message
    │
    ▼
chat_service.send_chat_message()
    │
    ├─ 1. Validate session exists
    ├─ 2. Persist user message
    ├─ 3. chat_tools.try_handle_tool_command()  ◄── fast-path
    │       ├─ Matched? → return instant response (no LLM)
    │       └─ Not matched? → continue
    ├─ 4. Resolve explicit doc_chunk_ids (if any)
    ├─ 5. chat_engine.generate_reply()
    │       ├─ RAG search (embeds query, cosine similarity, top_k=3)
    │       ├─ _complete_with_tools() → LLM call + tool execution (incl. search_wikipedia)
    │       └─ Returns plain text
    └─ 6. Persist assistant reply, return to caller
```

### Streaming (`POST /chat/sessions/{id}/messages/stream`)

```
User message
    │
    ▼
chat_service.send_chat_message_stream()
    │
    ├─ 1. Validate session exists
    ├─ 2. Persist user message
    ├─ 3. chat_tools.try_handle_tool_command()  ◄── fast-path
    │       ├─ Matched? → yield token + done, return
    │       └─ Not matched? → continue
    ├─ 4. Resolve explicit doc_chunk_ids (if any)
    ├─ 5. chat_engine.generate_reply_stream()
    │       ├─ RAG search (embeds query, cosine similarity, top_k=3)
    │       ├─ _complete_with_tools_stream() → streaming LLM + tools (incl. search_wikipedia)
    │       └─ Yields (event_type, data) tuples
    ├─ 6. Re-yield as SSE dicts: {"event": ..., "data": ...}
    └─ 7. Accumulate full_reply, persist when done
```

### SSE Event Types

| Event | Data | When |
|-------|------|------|
| `token` | Text fragment | Model is generating content |
| `tool` | `"Executed tool: <name>"` | A tool call was executed |
| `plan_step` | Phase description | Lesson job planner is working |
| `subagent_start` | Subtask name | A worker/reviewer/saver subtask started |
| `subagent_done` | Subtask name | A subtask completed successfully |
| `subagent_error` | Error message | A subtask failed |
| `artifact` | JSON payload | A generated part/quiz is ready |
| `chat_status` | Status message | General progress update during lesson generation |
| `done` | `""` | Response complete |
| `error` | Error message | Something went wrong |

---

## Module: `chat_tools.py`

**Purpose**: Intercept common user intents before they reach the LLM, returning instant responses. Also handles lesson/quiz orchestration with auto-QA generation and DB persistence.

**Entry point**: `try_handle_tool_command(user_text, account_id, user_dek) -> str | None`

Returns a response string if the intent is recognized, or `None` to fall through to the LLM.

### Key Internal Functions

| Function | Description |
|----------|-------------|
| `_load_qa(account_id, user_dek)` | Load QA data with DB → disk fallback chain |
| `_load_qa_from_db(account_id, user_dek)` | Load QA from `AccountArtifactModel` (stateless DB mode) |
| `_save_qa_to_db(account_id, qa, user_dek)` | Persist QA artifact to DB (stateless mode, upserts with metadata) |
| `_try_generate_qa(account_id, user_dek)` | Auto-generate QA from uploaded documents via `CurriculumQAPipeline` |
| `_handle_quiz(topic, account_id, user_dek)` | Build standalone quiz with auto-generate fallback |
| `_handle_lesson_quiz(account_id, user_dek)` | Build checkpoint quiz with auto-generate fallback |

### Routing Order

1. **Slash commands** — exact prefix match on `/`
2. **Natural language patterns** — substring matching against known phrases
3. **Active quiz fallback** — if quiz is active, treat unrecognized input as an answer (stale quizzes auto-clear after 2 hours)
4. **Fall through** — returns `None`

### Supported Commands

| Command | Pattern Examples | Handler |
|---------|-----------------|---------|
| Dashboard | `/dashboard`, "show my stats", "how am i doing" | `_handle_dashboard()` |
| Start lesson | "start lesson on X", "teach me about Y" | `_handle_start_lesson()` |
| Lessons | `/lessons`, `/lesson`, "my lessons", "continue lesson" | `_handle_lessons()` |
| Lesson continue | `/lesson continue`, `/lesson continue <index>` | `_handle_continue_lesson()` |
| Lesson quiz | `/lesson quiz` | `_handle_lesson_quiz()` |
| Lesson open | `/lesson open <index>` | `_handle_open_lesson()` |
| Quiz | `/quiz <topic>`, "quiz me on X" | `_handle_quiz()` |
| Documents | `/docs`, "show my docs", "what documents" | `_handle_docs()` |
| Help | `/help`, "what can you do" | `_handle_help()` |
| Settings | `/settings get`, `/settings set system_prompt ...` | `_handle_settings()` |
| Review | `/review` | Shows quiz progress |
| Exit | `/exit` | Exits active quiz early |
| Submit | `/submit` | Submits quiz for grading |

### Lesson System

Lessons use LLM-generated content with embedded quizzes. All lesson creation routes through the harness — the legacy QA pool path is removed.

#### Lesson Harness (Only Path)

1. **Generate lesson** → `POST /lessons/generate` starts async job (or chat `/start lesson on X`)
2. **Collection** → gathers context from user documents (or Wikipedia fallback)
3. **Planning** → planner LLM produces 1–5 part curriculum plan
4. **Generation** → parts generated in parallel (up to 3 workers) with optional web search
5. **Review** → reviewer LLM approves or triggers regeneration
6. **Save** → saved to `AccountStore` with embedded quiz data in checkpoints
7. **Lesson quiz** → reads `checkpoint.quiz` embedded data; fails if missing
8. **Grade** → `POST /lessons/{id}/checkpoints/{index}/grade` grades via `grade_lesson_quiz()`
   - MCQ: exact match on `correct_option_index`
   - Open: semantic 70% + lexical 30%, threshold 0.58
9. **Advance** → checkpoint advances on pass

> The legacy `build_checkpoint_quiz()` is disabled — all new lessons require harness-generated embedded quizzes.

### Auto-QA Generation

When `/quiz <topic>` is called but no QA data exists:
1. Loads uploaded documents via `list_docs()`
2. Extracts text chunks as rows
3. Runs `CurriculumQAPipeline.run()` to generate curriculum + QA
4. Saves output to account's QA artifact path (disk)
5. In stateless DB mode (Postgres), persists the artifact to `AccountArtifactModel` via `_save_qa_to_db()` so the `POST /quiz/build` API endpoint can find it
6. Builds quiz from the generated data

### Pattern Detection

Pattern detectors use simple substring matching (not regex) for speed:

- `_is_dashboard_request(text)` — matches "dashboard", "stats", "progress", etc.
- `_is_lesson_request(text)` — matches "lessons", "resume", "continue", etc.
- `_is_docs_request(text)` — matches "docs", "documents", "uploaded", etc.
- `_is_help_request(text)` — matches "help", "what can you do"

**Trade-off**: Substring matching can false-positive on unrelated messages containing these words. This is acceptable because the responses are read-only (dashboard/lessons/docs) and the user can always rephrase.

---

## Module: `chat_service.py`

**Purpose**: Orchestrate the chat pipeline — validate, persist, route, respond.

### Key Functions

#### `send_chat_message(session_id, payload, account_id, user_dek) -> SendMessageResponse`

Non-blocking chat. Returns the full reply as a string.

Flow:
1. Validate session → 404 if missing
2. Persist user message
3. Fast-path check (chat_tools)
4. Resolve explicit doc_chunk_ids (if any)
5. LLM call via `chat_engine.generate_reply()` (RAG search happens inside)
6. Persist assistant reply

#### `send_chat_message_stream(session_id, payload, account_id, user_dek)`

Streaming chat. Yields SSE event dicts.

Same flow as above, but step 5 uses `chat_engine.generate_reply_stream()` and the service re-yields events as `{"event": type, "data": content}`.

If the LLM response contains a `create_lesson` tool result with a `lesson_job_*` ID, the stream transparently switches to polling the lesson generation job status (up to `OBITAL_LESSON_JOB_STREAM_TIMEOUT_SEC`, default 120s). Yields `plan_step`, `subagent_start`, `subagent_done`, `subagent_error`, `artifact`, and `chat_status` events.

#### `confirm_chat_action(session_id, action_id, decision, account_id, user_dek)`

Confirms or cancels a pending agent tool action (mutation safety). On approval, calls `execute_tool()` from `study_tools.py`.

---

## Module: `chat_engine.py`

**Purpose**: LLM integration with tool calling, streaming, DSML tag handling, and thinking mode support.

### Architecture

The engine communicates with a DeepSeek-compatible LLM via the OpenAI SDK. It supports:

- **Tool calling** — the LLM can call functions (get settings, list lessons, etc.)
- **Dynamic tool spec** — quiz tools only included when there's an active quiz (`_build_tool_spec()`)
- **DSML fallback** — if the model outputs DSML XML tags instead of proper tool calls, they are parsed and executed
- **Streaming** — both LLM calls stream content to the user in real-time
- **Thinking mode** — when `DEEPSEEK_THINKING_ENABLED=true`, sends `reasoning_effort` + `extra_body={"thinking": {"type": "enabled"}}` and captures `reasoning_content`

### Public Functions

#### `generate_reply(user_message, lesson, doc_texts, account_id, user_dek) -> tuple[str, int]`

Non-streaming chat. Returns the complete reply as a string and token count.

1. If `doc_texts` is empty → run RAG search (embed query, cosine similarity, top_k=3)
2. Build context via `HybridContextBuilder`
3. Call `_complete_with_tools()` — single LLM call with tool support (LLM can call `search_wikipedia` if it needs external knowledge)
4. If LLM fails, return a local fallback message

#### `generate_reply_stream(user_message, lesson, doc_texts, account_id, user_dek)`

Streaming chat. Yields `(event_type, data)` tuples.

Same as above but uses `_complete_with_tools_stream()` which streams both LLM calls. Yields `"thinking"` events when reasoning content is available.

### Internal Functions

#### `_cached_embed_text(text) -> tuple[float, ...]`

LRU-cached wrapper (128 entries) around `embeddings.embed_text()`. Returns an immutable tuple for cacheability.

#### `_search_relevant_chunks(query, account_id, user_dek) -> list[str]`

RAG search. Embeds the query via `_cached_embed_text()`, runs cosine similarity via `search_chunks_by_vector()` (top_k=3, threshold=0.35). Returns matching chunk texts or empty list.

#### `_search_wikipedia(query) -> list[str]`

Rate-limited Wikipedia search (10 calls/60 seconds). Called by the `search_wikipedia` LLM tool. Calls the MediaWiki API search endpoint, strips HTML from snippets, returns up to 3 `"Title: snippet"` strings. Returns empty list on rate limit or failure.

#### `_thinking_kwargs(router) -> dict`

Builds `reasoning_effort` + `extra_body` kwargs when thinking mode is enabled.

#### `_build_tool_spec(account_id) -> list[dict]`

Returns `_BASE_TOOLS` (always) + `_QUIZ_TOOLS` (only when active quiz exists). Prevents the LLM from calling quiz tools when there's no quiz.

#### `_complete_with_tools(router, messages, account_id, user_dek) -> str`

Non-streaming LLM call with tool execution:

1. Make LLM call with dynamic tools and thinking kwargs
2. If tool calls returned → execute them, make followup call (with reasoning_content preserved)
3. If DSML tags in content → parse and execute as tool calls
4. If no tools → return cleaned content

#### `_complete_with_tools_stream(router, messages, account_id, user_dek)`

Streaming LLM call with tool execution:

1. Stream first LLM call, accumulate `raw_reasoning` + `raw_content` + tool call deltas
2. Yield `"thinking"` events for reasoning content
3. If tools detected → yield content, execute tools, stream followup (with reasoning preserved)
4. If no tools → yield content

### DSML Handling

DSML (DeepSeek Markup Language) is a fallback mechanism for models that don't support native tool calling. The engine detects DSML tags in model output:

```
<｜｜DSML｜｜invoke name="tool_name">
  <｜｜DSML｜｜parameter name="param">value</｜｜DSML｜｜parameter>
</｜｜DSML｜｜invoke>
```

These are parsed by `_parse_dsml_tool_calls()` and executed like standard tool calls.

### Tool Spec

Tools are split into `_BASE_TOOLS` (always available) and `_QUIZ_TOOLS` (only when active quiz exists). Built dynamically via `_build_tool_spec(account_id)`.

**Base tools** (always available):

| Tool | Description | Parameters |
|------|-------------|------------|
| `get_system_prompt` | Read the current tutor system prompt | none |
| `update_system_prompt` | Replace the tutor system prompt | `system_prompt: string` |
| `get_dashboard_summary` | Get student's points, quizzes, accuracy | none |
| `list_lessons` | List lessons with status and checkpoint info | none |
| `list_docs` | List uploaded documents | none |
| `open_doc_excerpt` | Get text from a document by index or name | `doc_index: int?`, `doc_name: string?` |
| `create_lesson` | Start a new lesson on a topic | `topic: string`, `title: string?` |
| `search_wikipedia` | Search Wikipedia for external knowledge (rate-limited: 10/min) | `query: string` |

**Quiz tools** (only when active quiz):

| Tool | Description | Parameters |
|------|-------------|------------|
| `submit_answer` | Submit answer to current quiz question | `answer: string` |
| `review_quiz` | Show quiz progress and answers | none |
| `exit_quiz` | End quiz early, show partial results | none |
| `submit_quiz` | Finalize and grade quiz | none |

### Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `CHAT_TOOL_TIMEOUT_SEC` | 12.0 | Timeout for each LLM call |
| `MAX_TOOL_CALLS_PER_TURN` | 5 | Max tool calls per LLM response |
| `_dsml_call_counter` | `itertools.count()` | Unique IDs for DSML-parsed tool calls |
| `_RAG_TOP_K` | 3 | Number of RAG chunks to retrieve |
| `_RAG_THRESHOLD` | 0.35 | Minimum cosine similarity for RAG matches |
| `_WIKI_MAX_CALLS` | 10 | Max Wikipedia API calls per window |
| `_WIKI_WINDOW_SEC` | 60.0 | Rate limit window in seconds |

---

## Module: `storage.py`

**Purpose**: Data access layer for all persistent state.

### Dual Storage Modes

The system supports two storage backends:

1. **Filesystem** — JSON files under `backend/data/`
2. **Database** — SQLAlchemy with SQLite or PostgreSQL (encrypted with E2EE)

The mode is determined by `db_enabled()` / `is_db_mode()`.

### Key Functions

| Function | Description |
|----------|-------------|
| `list_account_lessons(account_id)` | List lessons from account store with progress |
| `list_lessons(account_id, user_dek)` | List lessons from curriculum QA data |
| `list_docs(account_id, user_dek)` | List uploaded documents |
| `get_doc(doc_id, account_id, user_dek)` | Get a single document with chunks |
| `search_docs(query, account_id, user_dek, filter_tags)` | Search docs by name or content, optionally filtered by tags |
| `search_chunks_by_vector(query_vector, ..., filter_tags)` | Cosine similarity search over chunk embeddings, optionally pre-filtered by tags |
| `save_embeddings(doc_id, embeddings_data, ..., doc_tags)` | Save chunk embeddings JSON; accepts optional `doc_tags` list written into the payload |
| `get_rewards_dashboard(account_id, limit)` | Get quiz stats and trend data |
| `create_session(account_id)` | Create a new chat session |
| `get_session(session_id, account_id)` | Get session with messages |
| `append_message(session_id, message, ...)` | Append a message to a session |
| `delete_doc(doc_id, account_id, user_dek)` | Soft-delete document → creates BinItemModel row (30-day TTL) |
| `restore_doc(bin_item_id, account_id, user_dek)` | Restore document from bin (clears deleted_at/expires_at on artifact rows) |
| `list_bin_items(account_id)` | List all non-expired bin items ordered by deletion date |
| `permanently_delete_expired(account_id)` | Purge expired bin items and their artifact rows/files |
| `permanent_delete_bin_item(bin_item_id, account_id)` | Immediately delete a single bin item and its artifacts |

### E2EE (End-to-End Encryption)

When using PostgreSQL (stateless DB mode), all document content and embeddings are encrypted with the user's DEK (Data Encryption Key) before storage. The `user_dek` parameter is threaded through all storage functions.

### Tagging

Documents and lessons carry `tags: list[str]` for hybrid search. Tags are stored in the `.embeddings.json` payload alongside vectors, auto-extracted by LLM on upload (`_extract_doc_tags()` in `api/doc.py`) or derived from topic/title on lesson read (`_derive_lesson_tags()` in `storage.py`). Search functions accept `filter_tags` for pre-filtering before vector/keyword matching.

---

## Bin System

The bin is a soft-delete / trash system with a configurable TTL (default 30 days).

### Data Model

Defined in `models.py` (`BinItemModel`, table `bin_items`):

| Column | Type | Description |
|--------|------|-------------|
| `id` | str (PK) | UUID hex (16 chars) |
| `account_id` | str | Owning account |
| `item_type` | str | `"document"` or `"lesson"` |
| `item_id` | str | Original document/lesson ID |
| `name` | str | Display name |
| `metadata_json` | text | JSON payload (relative_path, state, etc.) |
| `deleted_at` | datetime | When the item was trashed |
| `expires_at` | datetime | Auto-delete deadline |

### Lifecycle

```
Delete document → move artifacts to bin_items with 30-day expiry
     │
     ├─ Restore (POST /bin/{id}/restore)
     │    ├─ Document → clear deleted_at/expires_at on artifact rows
     │    └─ Lesson  → restore lesson state via AccountStore
     │
     ├─ Permanent delete (DELETE /bin/{id})
     │    └─ Remove bin row + artifact rows/files immediately
     │
     └─ Auto-cleanup (bin_cleanup.py daemon, every 3600s)
          └─ permanently_delete_expired() → purge + remove artifacts
```

### Storage Functions

| Function | Description |
|----------|-------------|
| `delete_doc()` | Marks artifact rows as deleted, creates `BinItemModel` entry with `expires_at = now + 30 days` |
| `restore_doc()` | Looks up bin row → clears `deleted_at`/`expires_at` on matching artifacts → deletes bin row |
| `list_bin_items()` | Queries `bin_items` for account, returns items with computed `days_remaining` |
| `permanently_delete_expired()` | Deletes all `bin_items` where `expires_at < now`, removes associated artifact rows/files |
| `permanent_delete_bin_item()` | Immediately removes a single bin row + its artifact rows/files |

### Bin Cleanup Daemon (`bin_cleanup.py`)

- Runs as a background `threading.Thread` (name `bin-cleanup`)
- Configurable interval via `BIN_CLEANUP_INTERVAL_SECONDS` env var (default 3600s)
- Started/stopped by `start_bin_cleanup()` / `stop_bin_cleanup()` in the FastAPI lifespan
- Calls `permanently_delete_expired()` each cycle; logs count on non-zero deletions

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/bin` | List bin items with days remaining |
| `POST` | `/bin/{bin_id}/restore` | Restore document or lesson from bin |
| `DELETE` | `/bin/{bin_id}` | Permanently delete a bin item |

### Frontend Integration

- **React SPA**: `BinFullScreen.tsx` — full-screen view with restore/delete buttons, accessed via right-panel `Bin` tab
- **Textual TUI**: `/bin` lists items, `/bin restore <id>` restores
- **TypeScript API**: `bin.ts` — `listBin()`, `restoreBinItem(binId)`, `permanentDeleteBinItem(binId)`
- **Python API client**: `api_client.py` — `list_bin()`, `restore_bin_item(bin_id)`, `permanent_delete_bin_item(bin_id)`
- **Toast notifications**: Doc/Lesson delete shows "Document/Lesson moved to bin"

---

## Lesson API (`api/lesson.py`)

CRUD endpoints for lesson lifecycle management.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/lessons` | List lessons (`?source=account` or `?source=curriculum`). Supports `limit` (1–100) and `offset` (≥0) |
| `GET` | `/lessons/resolve?q=...` | Fuzzy lesson lookup by title or ID |
| `GET` | `/lessons/{lesson_id}` | Get a lesson by exact ID |
| `POST` | `/lessons` | Start a new lesson on a topic |
| `POST` | `/lessons/generate` | Start background lesson generation via harness (returns job ID immediately) |
| `GET` | `/lessons/jobs` | List recent generation jobs for the account |
| `GET` | `/lessons/jobs/{job_id}` | Poll lesson generation job status with subtask progress |
| `POST` | `/lessons/jobs/{job_id}/cancel` | Cancel an in-flight generation job |
| `POST` | `/lessons/{lesson_id}/restart` | Reset checkpoint progress |
| `POST` | `/lessons/{lesson_id}/attempt` | Record a checkpoint attempt |
| `POST` | `/lessons/{lesson_id}/checkpoints/{index}/grade` | Grade checkpoint answers against embedded quiz data |
| `DELETE` | `/lessons/{lesson_id}` | Soft-delete lesson (moves to bin with 30-day TTL) |

### Lesson Lifecycle

**Harness generation flow:**
```
POST /lessons/generate {"topic": "binary trees"}
    │
    ▼
Background job created → returns {job_id, status: "queued"}
    │
    ▼
Poll GET /lessons/jobs/{job_id} (or stream via SSE chat)
    │
    ├─ status: "planning"     → planner LLM building curriculum
    ├─ status: "generating"   → parallel worker LLMs creating parts
    ├─ status: "reviewing"    → reviewer LLM checking quality
    ├─ status: "saving"       → persisting to AccountStore
    └─ status: "succeeded"   → lesson ready with embedded quizzes + tags
```

Tags are derived from topic and checkpoint titles via `_derive_lesson_tags()` and returned in the lesson response as `lesson.tags`.

**Quiz grading flow:**
```
POST /lessons/{id}/checkpoints/{index}/grade
    │
    ▼
Grade checkpoint answers against embedded quiz data
    │
    ├─ MCQ: exact match on correct_option_index
    ├─ Open: semantic (70% embed cosine) + lexical (30% F1 overlap)
    └─ Threshold: CORRECTNESS_THRESHOLD = 0.58
    │
    ▼
Returns GradeCheckpointResponse {passed, score_percent, results}
```

**Chat flow (via `chat_tools.py`):**
```
"start lesson on binary trees"
    │
    ▼
create_lesson_payload(use_harness=True) → returns {harness_job_id}
    │
    ▼
Chat streams job events: plan_step → subagent_start → ... → artifact → done
    │
    ▼
Lesson saved with embedded quiz → "Type /lesson quiz"
    │
    ▼
/lesson quiz → reads checkpoint.quiz embedded data
    │
    ▼
Answer questions → graded via submit_answer_payload()
    │
    ▼
Last question → auto-submit → grade_quiz() → apply_lesson_attempt()
    │
    ├─ passed → checkpoint advances
    └─ failed → retry with /lesson quiz
```

### Checkpoints

Default checkpoints (if not provided in `POST /lessons`):

| # | Title | Difficulty | Questions | Pass Threshold |
|---|-------|------------|-----------|----------------|
| 1 | `{topic} fundamentals` | easy | 2 | 60% |
| 2 | `{topic} application` | medium | 3 | 70% |
| 3 | `{topic} mastery` | hard | 3 | 75% |

Custom checkpoints can be provided in the `POST /lessons` request body.

---

## Usage & Limits (`usage.py` + `api/usage.py`)

Tracks per-account resource usage and enforces limits.

### Limits

| Resource | Constant | Default | Reset Period |
|----------|----------|---------|-------------|
| Tokens | `MAX_TOKENS` | 100,000 | 30 days |
| Documents | `MAX_DOCS` | 50 | Never |
| Lessons | `MAX_LESSONS` | 100 | Never |
| Storage | `MAX_STORAGE_BYTES` | 100 MB | Never |

Token usage resets lazily: checked on every `get_usage()` call, resets `tokens_used` to 0 if `RESET_PERIOD` has elapsed since `usage_reset_at`.

### Storage

Usage counters are stored in the user's profile JSON (`profile_json` in `AccountRegistryRecordModel`) alongside existing fields like `total_points`, `total_quizzes`, etc. This requires no DB migration.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/usage` | Returns current usage and limits as `AccountUsage` |
| `POST` | `/usage/tokens` | Records token usage (checks limit first, 429 if over) |

### Integration Points

- **Chat**: `check_token_limit()` before LLM call, `record_token_usage()` after
- **Doc upload**: `check_doc_limit()` before save, `record_doc_upload()` after
- **Lesson start**: `check_lesson_limit()` before create, `record_lesson_started()` after

---

## Data Flow Diagram

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Frontend   │────▶│  API Routes  │────▶│   chat_     │
│  (SSE/REST)  │◀────│   chat.py    │◀────│  service.py │
└─────────────┘     └──────────────┘     └──────┬──────┘
                                                │
                              ┌─────────────────┼─────────────────┐
                              │                 │                 │
                              ▼                 ▼                 ▼
                    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
                    │  chat_tools  │  │ chat_engine  │  │   storage    │
                    │  (fast-path) │  │ (RAG + wiki) │  │  (data access)│
                    │  + auto-QA   │  │  (LLM + tools)│  └──────────────┘
                    │  + DB persist│  └──────┬───────┘
                    └──────────────┘         │
                                      ┌──────┴───────┐
                                      │              │
                                      ▼              ▼
                               ┌───────────┐  ┌──────────────┐
                               │DeepSeek API│  │ Wikipedia API│
                               │ (OpenAI SDK)│  │ (fallback)   │
                               └───────────┘  └──────────────┘
                                                               │
                                     ┌──────────────┐         │
                                     │  DB (Postgres)│◀────────┘
                                     │  or Disk      │
                                     └──────────────┘
```

---

## Adding New Commands

To add a new fast-path command:

1. Add a pattern detector in `chat_tools.py`:
   ```python
   def _is_new_feature_request(text: str) -> bool:
       return text in {"new feature", "/new-feature"}
   ```

2. Add a handler:
   ```python
   def _handle_new_feature(*, account_id: str) -> str:
       return "Feature response here"
   ```

3. Wire it into `try_handle_tool_command()`:
   ```python
   if _is_new_feature_request(lowered):
       return _handle_new_feature(account_id=account_id)
   ```

4. Add the slash command to `_handle_slash()` if applicable.

No LLM changes needed — the fast-path runs before the model is called.

---

## Adding New LLM Tools

To add a new tool the LLM can call:

1. Add the tool spec to `CHAT_TOOL_SPEC` in `chat_engine.py`
2. Add a handler in `_tool_result_payload()`
3. The LLM will automatically discover and use the tool

---

## Performance Notes

- **Fast-path routing**: ~0ms (string matching)
- **LLM first call**: 1-5s (depending on model + complexity)
- **Tool execution**: ~10-100ms per tool (storage reads)
- **LLM followup** (after tools): 1-5s
- **Streaming first token**: 200-800ms (time to first token)
- **Lesson generation (harness)**: 20-120s total (context collection + planner + parallel workers + reviewer)

The fast-path avoids the 2-10s LLM round-trips for common requests like dashboard, lessons, and docs.
