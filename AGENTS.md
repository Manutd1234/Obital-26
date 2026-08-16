# Authored by Noah — https://github.com/noah-e1528170

# Architecture Decisions & Plans

## Quiz Generation: Lesson Harness vs Engine

### Decision
Move quiz **generation** into `quiz_harness.py` (LLM-generated via `generate_quiz()`),
used by both standalone `/quiz` and lesson pipelines.
Route grading through `engine.quiz.quiz_backend.grade_quiz()`.
Deprecate `quiz_backend.build_quiz()` (QA pool selector) and `quiz_service.build_topic_quiz()`.

### Rationale
| Concern | quiz_harness (LLM) | Engine QA pool (`build_quiz`) |
|---------|----------------------------|-------------------------------|
| Quiz relevance | Tailored to topic via LLM | Generic, disconnected from lesson |
| Grading | Robust via `grade_quiz()` | Robust via `grade_quiz()` |
| Reusability | Per-session (LLM-generated) | Per-session (pool selection) |
| Unified pipeline | Single path for all quizzes | Separate path for standalone |

---

## Lesson Harness Architecture

### Overview

`lesson_harness.py` is an LLM-powered lesson generation engine that runs as a background thread. It produces complete multi-checkpoint lessons with embedded quizzes, markdown content, and source metadata — all tailored to the user's uploaded documents.

### Generation Pipeline

```
create_lesson_generation_job()
  └─ creates job in JsonJobStore (status="queued")
  └─ spawns daemon thread → _run_lesson_generation_job()
      └─ _generate_lesson() [async, asyncio.run()]
          ├─ _preflight_rejection()         — rejects broad/cheating topics
          ├─ _collect_context()             — searches user documents
          ├─ _collect_wikipedia_context()   — Wikipedia fallback if no local docs
          ├─ _refine_context()              — LLM filters relevant chunks
          ├─ planner LLM call               — produces 2-5 part curriculum plan
          ├─ _generate_part_worker() × N    — parallel LLM part generation
          │   └─ LLM produces: title, difficulty, markdown (≥900 chars), quiz[Q], sources[]
          │   └─ _normalize_part()          — validates + normalizes output
          │   └─ quiz_harness.normalize_quiz() — normalizes quiz to QuizQuestion dicts
          ├─ quiz_harness.find_part_quality_issues() — checks length, non-empty quiz, no dupes
          ├─ reviewer LLM call              — approves or triggers regeneration
          └─ _save_generated_lesson()       — AccountStore.start_lesson() with embedded quiz
```

### Checkpoint Data Structure

Each checkpoint saved to `AccountStore` contains:
```
{
  "title": str,
  "difficulty": "easy" | "medium" | "hard",
  "num_questions": int,
  "pass_threshold_percent": float (default 70.0),
  "question_mode": "mcq" | "open" | "mixed",
  "markdown": str (lesson content),
  "quiz": [                                      # ← embedded by harness
    {
      "question_id": str,
      "topic_id": str,
      "topic_title": str,
      "question": str,
      "question_mode": "mcq" | "open",
      "options": [str, ...],                     # empty for open-ended
      "correct_option_index": int | None,
      "expected_answer": str,
      "explanation": str,
      "difficulty": str,
      "source_context": str,
    },
    ...
  ],
  "sources": [{"title": str, "url": str, ...}],
  "context_source": "documents" | "wikipedia" | "primer",
  "generation_metadata": {...}
}
```

### Quiz Normalization (`_normalize_quiz`)

Converts raw LLM quiz output to structured `QuizQuestion`-compatible dicts:
1. Creates `topic_id` from topic name
2. Iterates items (max 5), skips non-dict/missing question
3. Normalizes options (max 4), infers `correct_option_index` if missing via `expected_answer` match
4. Fills `expected_answer` from correct option text if available, else part's goal
5. Sets `source_context` via `_best_source_context()`
6. Validates through Pydantic `QuizQuestion` model
7. Falls back to single goal-based question if no valid questions

---

## Data Flow

### A. Lesson Creation

All entry points converge on `create_lesson_payload(topic, use_harness=True)` in `chat_tools.py`:

```
User → chat_tools.py:_handle_start_lesson()
     → chat_engine.py:create_lesson (AI tool)
     → study_tools.py:create_lesson (agent tool)
       └─ all call: chat_tools.create_lesson_payload(use_harness=True)
           └─ lesson_harness.create_lesson_generation_job()
               └─ returns {harness_job_id: str} — non-blocking, runs in background
```

### B. Quiz Presentation

```
User: "/lesson quiz"
  → chat_tools._handle_lesson_quiz()
    → checkpoint.get("quiz")?  YES → build QuizQuestions from embedded data → set_active_quiz()
    → checkpoint.get("quiz")?  NO  → "Request lesson regeneration with harness"
```

### C. Quiz Grading (API)

```
POST /lessons/{id}/checkpoints/{index}/grade
  → lesson.py:grade_checkpoint()
    → authenticates via require_account
    → validates lesson + checkpoint from AccountStore
    → quiz_service.grade_lesson_quiz(account_id, lesson_id, checkpoint_index, answers)
      → reconstructs QuizQuestion from embedded checkpoint["quiz"]
      → builds QuizBuildResponse
      → calls quiz_backend.grade_quiz() for scoring
        └─ MCQ: exact match on correct_option_index
        └─ Open: semantic (70% embed cosine) + lexical (30% F1 token overlap)
        └─ Threshold: CORRECTNESS_THRESHOLD = 0.58
    → records attempt via AccountStore.apply_lesson_attempt()
    → returns GradeCheckpointResponse {passed, score_percent, results}
```

### D. Quiz Grading (Chat — per-question)

```
User answers in chat
  → chat_tools.submit_answer_payload()
    → grades via quiz_backend._grade_question_answer()
    → stores in quiz_state
    → if last: auto-submit → grade_quiz() → apply_lesson_attempt()
    → else: advance to next question
```

---

## Phase Status

### Phase 1: Align quiz formats ✅
- `_normalize_quiz()` outputs `QuizQuestion`-compatible dicts
- `grade_lesson_quiz()` reconstructs `QuizBuildResponse` from embedded data
- `POST /lessons/{id}/checkpoints/{index}/grade` endpoint exists

### Phase 2: Connect old commands to harness ✅
- All lesson commands route through harness (see Data Flow above)
- QA pool fallback **already removed** from `chat_tools._handle_lesson_quiz()` and `quiz_chatbot_cli._run_active_lesson_checkpoint()`

### Phase 3: Deprecate old paths (remaining)
| Item | File | Status |
|------|------|--------|
| `build_checkpoint_quiz()` | `quiz_service.py` | Still exists (deprecated) |
| `build_topic_quiz()` | `quiz_service.py` | **Deprecated** — raises, replaced by `quiz_harness.generate_quiz()` |
| `_default_lesson_checkpoints()` | `quiz_chatbot_cli.py` | Still exists (deprecated) |
| `_start_lesson_from_prompt()` | `quiz_chatbot_cli.py` | Still exists (deprecated) |
| `use_harness=False` path | `chat_tools.py` | Still exists (deprecated) |
| `QuizBuildRequest` dependency in lesson flows | multiple | Still present |
| CLI fallback to QA pool | `quiz_chatbot_cli.py` | **Already removed** |
| Chat tools fallback to QA pool | `chat_tools.py` | **Already removed** |
| Chat tools `/quiz` → QA pool | `chat_tools.py` | **Replaced** — now routes through `quiz_harness.generate_quiz()` |
| API `/quiz/build` → QA pool | `api/quiz.py` | **Replaced** — now routes through `quiz_harness.generate_quiz()` |

---

## Jun 2026 — Artifact Hydration & Dashboard Performance

### Decision
Parallelize artifact decryption during hydration, skip expensive text extraction when content isn't needed, remove blocking doc listing from bootstrap, and make the frontend dashboard render progressively.

### Changes

| # | What | File | Impact |
|---|------|------|--------|
| 1 | **Parallel decryption** | `account_sync.py` | Decrypt artifact bodies via `ThreadPoolExecutor(4)` instead of sequentially. Extracts cloud ORM data into plain tuples first (thread-safe). |
| 2 | **Hydration logging** | `account_sync.py` | Added `logger.info` at start (artifact count, bytes, workers) and end (hydrated/failed/remaining, elapsed seconds). Previously only failure warnings existed. |
| 3 | **Skip text extraction when `include_content=False`** | `storage.py:_read_uploaded_docs()`, `_read_raw_text_docs()` | Both functions now accept `include_content=` param. When `False`, they skip reading file content and chunking — only return metadata. **Reverted** — caused chunk_count=0 for local-only docs. Text extraction is already LRU-cached. |
| 4 | **Remove `list_docs()` from bootstrap** | `auth.py` | Bootstrap was blocking on `list_docs()` synchronously. Frontend already fetches docs via `GET /docs` on mount (`ChatView`, `DashboardFullScreen`). Bootstrap now returns `docs: []`. |
| 5 | **Frontend uses global stores** | `DashboardFullScreen.tsx` | Dashboard reads docs from `useDocStore` and lessons from `useLessonStore` (pre-populated by `ChatView`) instead of calling its own `listDocs()`/`listLessons()`. Eliminates 2 duplicate API calls. |
| 6 | **Skip duplicate doc fetch on mount** | `DocFullScreen.tsx`, `PipelineFullScreen.tsx` | Skip `listDocs()` if `useDocStore.docs` already populated. |
| 7 | **Progressive rendering + dash cache** | `DashboardFullScreen.tsx` | Replaced single `Promise.all` spinner with per-section loading states. Dashboard stats cached with 30s TTL for instant reopening. Curriculum from shared `lessonStore` (instant). |

### Current status on caching (extraction / embedding)

- **Text extraction**: `_cached_extract_text` uses `@lru_cache(maxsize=128)` keyed by `(path, mtime, size)`. `_get_cached_text_from_bytes` uses `(name, sha256)` dict. Both in-memory only.
- **Embedding generation**: No deduplication. `_generate_embeddings_background` always calls `embed_texts()` on every chunk. `save_embeddings` always overwrites.
- **Upload flow**: No content deduplication. Same file uploaded twice re-extracts, re-chunks, and re-embeds from scratch.

### Key Files
- `backend/app/services/account_sync.py` — `hydrate_account_artifacts()` (parallel decryption + logging)
- `backend/app/services/storage.py` — `_read_uploaded_docs()`, `_read_raw_text_docs()` (include_content param), `save_embeddings()`
- `backend/app/services/embeddings.py` — `embed_text()`, `embed_texts()`
- `backend/app/api/auth.py` — `bootstrap_account()` (no longer lists docs)
- `backend/app/api/doc.py` — `_generate_embeddings_background()`, `upload_doc()`
- `frontend/src/components/fullScreen/DashboardFullScreen.tsx` — progressive render, uses docStore
- `frontend/src/components/fullScreen/DocFullScreen.tsx` — skip duplicate doc fetch
- `frontend/src/components/fullScreen/PipelineFullScreen.tsx` — skip duplicate doc fetch
- `frontend/src/components/chat/ChatView.tsx` — primary doc fetcher (populates docStore)

---

## Jun 2026 — Dialectic Memory System

### Decision
Replace the static system prompt + raw `[-10:]` history dump with Honcho's three-layer context window and a background cadence-based profile updater. This gives long-term user modeling, session-aware context, and efficient token use without a secondary server.

### Context Assembly

The prompt is built in this order (stable prefix first for DeepSeek prompt caching):

```
SYSTEM INSTRUCTIONS (chat_system_prompt.md)     ← permanently cached
agent_skill.md                                   ← permanently cached
<memory-context>                                 ← changes every 5+ turns
  ### Who is this user
  [user_profile from AccountStore]
  ### How to communicate with this user
  [agent_profile from AccountStore]
</memory-context>
<dialectic-supplement>                           ← changes every turn
  [rolling session summary, in-memory]
</dialectic-supplement>

[last 5 raw messages, down from 10]              ← separate message objects
[current user message]
```

Variable content sits at the end of the system message so the stable prefix (system instructions + agent_skill) always hits cache.

### Storage

- `user_profile` / `agent_profile`: Markdown text stored as new keys in the existing `AccountRegistryRecordModel.profile_json` TEXT column (no schema change).
- Session summaries: In-memory `dict[session_id, str]` in `memory_service.py`. Ephemeral — regenerated on next turn if lost.

### Profile Audit (Background Cadence)

Every `OBITAL_PROFILE_AUDIT_INTERVAL` (default 5) user + assistant message pairs:

1. `chat_service.py` formats the decrypted transcript in the main thread (DEK available).
2. `memory_service.maybe_audit_user_profile()` spawns a daemon background thread.
3. The thread calls `deepseek-chat` (configurable via `OBITAL_PROFILE_AUDIT_MODEL`) with the Dialectic Audit prompt.
4. The prompt returns delimited `===USER_PROFILE===` / `===AGENT_PROFILE===` sections.
5. Both profiles are written to `AccountStore` via a shared module-level singleton (`get_account_store()`) — the existing `_store_lock` serializes all reads/writes.
6. Profile truncation at sentence boundaries (8000 / 4000 chars).

### Session Summary (Per-Turn)

After every turn, `update_session_summary()` sends the old summary + new transcript to `deepseek-chat` for incremental compression. Old summary vs new summary prevents full re-summary each turn.

### Key Files

| File | Role |
|------|------|
| `backend/app/services/memory_service.py` | **New** — `build_dialectic_context()`, `get_session_summary()`, `update_session_summary()`, `maybe_audit_user_profile()`, `get_account_store()`, `_run_profile_audit()` |
| `backend/app/services/chat_engine.py` | Injects dialectic block via `_apply_system_prompt()`. Exports `get_router()`. History reduced to 5 turns. Uses shared `get_account_store()`. |
| `backend/app/services/chat_service.py` | Triggers `_trigger_dialectic_memory()` after every reply. Token estimate updated from `[-10:]` to `[-5:]`. |
| `engine/quiz/account_system.py` | `user_profile`/`agent_profile` fields + 4 getter/setter methods. |

---

## Jun 2026 — Friend System & Online Presence

### Architecture

```
AccountStore (friends[] in profile_json)  = who my friends are (persistent)
Supabase Realtime channel "room_social"   = who is online right now (live)
Backend heartbeat POST /presence/heartbeat = fallback last_seen state
Frontend useSocialPresence hook           = merges both into onlineFriendIds
```

### Friend Search & Add Flow

```
User types "alice"
  → GET /friends/search?q=alice
  → backend normalizes via normalize_account_id() (→ lowercase, 2-64 chars)
  → backend checks AccountStore.user_exists(target)
  → if exists: returns { account_id, display_name, already_friend }
  → if not: 404
```

**Key rules:**
- Source of truth for existence is `AccountStore.user_exists()`, not Supabase presence
- No public user directory — search is exact match only
- `normalize_account_id()` enforces `[a-z0-9][a-z0-9_-]{1,63}` lowercase
- `normalize_username()` in `account_system.py` lowercases + strips spaces

### Friend Storage

Stored in `profile_json.friends[]` — no separate DB table:

```json
{
  "friends": [
    {
      "account_id": "alice",
      "display_name": "alice",
      "added_at": "2026-06-25T..."
    }
  ]
}
```

### API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/friends` | List my friends |
| `GET` | `/friends/search?q=` | Exact-match user search |
| `POST` | `/friends/{friend_id}` | Add friend |
| `DELETE` | `/friends/{friend_id}` | Remove friend |
| `GET` | `/friends/presence` | Get online status of all friends (heartbeat) |
| `POST` | `/presence/heartbeat` | Send online/offline heartbeat |

### Online Detection (Two-Layer)

#### Layer 1: Supabase Realtime (primary)

```
Frontend (each tab):
  supabase.channel("room_social", { config: { presence: { key: normalized_username } } })
    .subscribe()
    .track({ status: "online", username, last_seen_at })
```

Events:
- `presence { event: 'sync' }` — full state refresh, filters to friend IDs only
- `presence { event: 'join' }` — friend came online → added to `onlineFriendIds`
- `presence { event: 'leave' }` — friend went offline → removed from `onlineFriendIds`

Spam protect: the shared `room_social` channel is cosmetic only; frontend filters to known friend IDs. Heartbeat is the trusted source.

#### Layer 2: Backend heartbeat (fallback)

```
Frontend every 25s (visible tab):
  POST /presence/heartbeat { status: "online" }
  → AccountStore.update_presence() → updates last_seen_at + presence_status

Frontend every 10s:
  GET /friends/presence
  → merges into onlineFriendIds (non-destructive — only adds, never removes)

Backend online rule (get_friends_presence):
  online = presence_status == "online" AND (now - last_seen_at) < 60s
```

#### Client lifecycle

```
AppShell mounts → useSocialPresence() fires
  → await fetchFriends()  // wait for friend list
  → setupPresenceChannel() // subscribe + track
  → startHeartbeat()       // 25s interval
  → presenceFallbackTimer  // 10s poll
  → visibilitychange listener

Tab hidden → sends offline heartbeat
Tab visible → re-tracks presence + sends online heartbeat
Tab close/unmount → untrack() + removeChannel() + offline heartbeat
```

### Race Condition Fixes

| Issue | Fix |
|-------|-----|
| Initial presence sync fires before friends loaded | `await fetchFriends()` before `setupPresenceChannel()` |
| Heartbeat poll overwrites realtime presence data | `fetchPresence()` merges (add-only), join/leave handle removal |
| Connection drop → no re-track | `CHANNEL_ERROR`/`TIMED_OUT` handlers; Supabase auto-reconnects |
| Track fails silently | Retry with exponential backoff (2s, 4s, 8s) up to 3 attempts |
| Case mismatch (raw username vs normalized account_id) | `normUsername = username.trim().toLowerCase()` used everywhere |

### Key Files

| File | Role |
|------|------|
| `backend/app/api/friends.py` | Friend CRUD + search endpoints |
| `backend/app/api/presence.py` | Heartbeat endpoint |
| `engine/quiz/account_system.py` | `friends[]` field, `add_friend()`, `remove_friend()`, `get_friends_presence()`, `update_presence()` |
| `frontend/src/hooks/useSocialPresence.ts` | Subscribes to room_social, tracks presence, heartbeat + fallback poll |
| `frontend/src/stores/friendStore.ts` | Zustand: friends list, onlineFriendIds, merge logic |
| `frontend/src/lib/supabase.ts` | Supabase client singleton (VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY) |
| `frontend/src/components/shared/FriendsModal.tsx` | UI: search, friend list, status dots, add/remove |
