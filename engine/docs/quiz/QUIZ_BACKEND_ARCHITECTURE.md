# Quiz Backend Architecture

## Purpose

The quiz subsystem converts offline curriculum/QA artifacts into an interactive quiz workflow:

- user prompt -> parsed quiz intent
- build quiz from stored QA content
- terminal Q&A session
- grade submission

Primary input artifact:
- `engine/output/output_curriculum_qa.json`
- (or custom `--qa-output` path such as `engine/output/my_curriculum_qa.json`)

## Modules

1. `quiz_backend.py`
   - Core tool functions: `build_quiz(...)`, `grade_quiz(...)`
   - CLI subcommands: `build-quiz`, `grade-quiz`
2. `quiz_intent.py`
   - Parses natural-language prompts into `QuizBuildRequest`
   - Extracts:
     - `topic` (positive topic intent)
     - `exclude_topic` (negative constraints like `not about trees`)
     - `difficulty`, `num_questions`, `question_mode`
3. `quiz_tui.py`
   - Text-based question flow in terminal (`/ask`, `/review`, `/explain`, `/dashboard`, navigation, submit/quit)
   - LLM intent routing with tool-calling and fallback JSON route mode
   - Router telemetry collection (per turn + session summary)
4. `quiz_chatbot_cli.py`
   - Chatbot loop that calls intent parser + backend + TUI
   - Handles top-level command guardrails (`/quit` exits, stray slash commands are rejected)
   - Handles dashboard requests at top-level and post-quiz follow-up prompts
   - Handles optional lesson mode entry/continuation/status commands
   - Persists router telemetry snapshots and history
5. `account_system.py`
   - Local account persistence, PIN auth, and point accumulation
   - Stores cumulative counters + bounded quiz history for dashboard/trend output
   - Stores lesson progression state (`active_lesson_id`, checkpoints, attempts)
6. `model_setup.py`
   - One-time/local-cache setup for embedding model download
7. `backend/app/services/chat_tools.py`
   - Fast-path intent router for chat UI (slash commands, natural language patterns)
   - Lesson/quiz orchestration with auto-QA generation
   - `_load_qa()` — DB → disk → auto-generate fallback chain
   - `_save_qa_to_db()` — persists auto-generated QA to DB in stateless mode
   - `_try_generate_qa()` — runs `CurriculumQAPipeline` from uploaded documents

## Runtime Boundaries

1. Offline generation layer (`main.py`, `qa_pipeline.py`)
   - Uses DeepSeek and RAG ingestion to produce `output_curriculum_qa.json`
2. Quiz runtime layer (`quiz_*`)
   - Builds and grades quizzes locally
   - Optional DeepSeek call path for LLM-based in-quiz intent routing
   - Loads existing artifact
3. Chat tools layer (`backend/app/services/chat_tools.py`)
   - Fast-path intent routing for web/textual chat UIs
   - Auto-generates QA from uploaded documents when artifact is missing
   - Persists auto-generated QA to DB (stateless mode) for API endpoint availability

## Web App Quiz Flow (Vercel-safe)

The web app (`backend/app/api/quiz.py`) uses a stateless, DB-backed quiz flow:

```
Browser                          Vercel (serverless)
──────                           ───────────────────
POST /quiz/build  ──────────────► _load_qa() reads QA from DB
  (account_id, topic, etc.)        → fallback: reads QA from disk
                                   → fallback: auto-generates QA from docs
                                   → validates CurriculumQAOutput
                                   → build_quiz() filters/shuffles/MCQ
◄────────────────────── QuizBuildResponse ──

User answers questions in browser...

Client-side grading (no server call)
  → gradeQuizLocally(quizData, quizAnswers)
  → MCQ: exact match + fuzzy typo matching
  → Open: lexical scoring + semantic embeddings (browser)
  → renderQuizResults()
```

**QA loading fallback chain** (same in both `api/quiz.py` and `chat_tools.py`):
1. DB lookup (stateless DB mode) — `AccountArtifactModel` with `relative_path == "output/output_curriculum_qa.json"`
2. Disk lookup — `account_qa_output_path()` filesystem path
3. Auto-generate — run `CurriculumQAPipeline` from uploaded documents, persist to DB and disk
4. Error — return 404 / "No curriculum QA data found"

**Key properties:**
- No `_quiz_cache` — server does not hold quiz state between requests
- No `/quiz/grade` endpoint — grading is fully client-side
- QA loaded from `AccountArtifactModel` (DB) or local disk, with E2EE decryption
- Auto-generated QA is persisted to DB in stateless mode (ensures `POST /quiz/build` works for subsequent requests)
- Embeddings computed in-browser via `@huggingface/transformers` (WebGPU/WASM)

## Build Status Contract

`build_quiz(...)` now returns explicit status fields in `QuizBuildResponse`:

- `build_indicator`:
  - `ok`
  - `fallback_used`
  - `failed`
- `failure_reason`:
  - `none`
  - `exclude_eliminated_all`
  - `insufficient_after_filters`
  - `generation_cap_reached`
  - `no_questions_available`

CLI behavior:
- `failed` does not enter quiz session.
- CLI prints reason-specific guidance.

## Build Logic

- Validate source payload as `CurriculumQAOutput`.
- Flatten topic QA into typed `QuestionCandidate` items.
- Apply topic filter (`topic_title` / `topic_id` substring match).
- Apply exclusion filter (`exclude_topic`).
- Apply difficulty filter (`easy|medium|hard`) with fallback to mixed.
- Apply question mode (`open` or `mcq`).
- Select requested number of questions.
- If count is short and fallback is allowed, synthesize bounded local question variants.
  - bounded by generated ratio cap (`MAX_GENERATED_RATIO = 0.4`)
  - bounded per-base variant count (`MAX_VARIANTS_PER_BASE = 1`)
- If constraints cannot be satisfied, return `failed` with explicit reason.
- Generate deterministic `quiz_id` via SHA-256 over:
  - request filters
  - selected question content fingerprint

## MCQ Distractor Generation

Distractors are scored by relevance to the correct answer:

1. Token overlap with correct answer (stop words excluded)
2. Length similarity penalty
3. Top 3 most relevant answers from the QA pool selected
4. Fallback chain when pool is too small:
   - "None of the above"
   - "All of the above"
   - "Not enough information to answer"
   - "The question is unclear"

## Question Rephrasing (Fallback Generation)

When too few questions match filters, variants are generated from existing questions.
Supported patterns with automatic rephrasing:

| Original Pattern | Rephrased |
|-----------------|-----------|
| `What is X?` | `In your own words, what is X?` |
| `What are X?` | `List the key characteristics of X.` |
| `Explain why X` | `Give a concise explanation of why X.` |
| `Explain how X` | `Describe the process of X.` |
| `How does X work?` | `Describe the mechanism by which X.` |
| `How do X?` | `Explain the steps involved in X.` |
| `Describe X` | `Summarize clearly: X.` |
| `Which of these is X?` | `Identify the correct statement about X.` |
| `When should you use X?` | `In what situations should you use X?` |
| `Where is X used?` | `Name the contexts where X is applicable.` |
| `Why is X important?` | `What is the significance of X?` |

Trailing verbs (e.g., "work", "traverse", "used", "important") are stripped from the subject
to avoid awkward grammar (e.g., "mechanism by which memory allocation", not "memory allocation work").

## Grading Constants

| Constant | Value | Used In |
|----------|-------|---------|
| `CORRECTNESS_THRESHOLD` | 0.58 | Both server and client — minimum score to mark an answer correct |
| `SEMANTIC_WEIGHT` | 0.7 | Blended open-answer score weight for semantic similarity |
| `LEXICAL_WEIGHT` | 0.3 | Blended open-answer score weight for lexical overlap |
| `TOKEN_TYPO_SIMILARITY` | 0.85 | Minimum normalized edit distance to accept a fuzzy token match |
| `MAX_GENERATED_RATIO` | 0.4 | Maximum fraction of quiz questions that can be fallback-generated variants |
| `MAX_VARIANTS_PER_BASE` | 1 | Maximum rephrased variants per base question |

## Grading Logic

### Server-side grading (CLI / TUI only)

Per question:

1. Compute lexical score:
   - token precision/recall
   - F1
   - lexical score = `0.7 * F1 + 0.3 * recall`
2. Compute semantic score:
   - embedding cosine similarity using `all-MiniLM-L6-v2`
   - division-by-zero guarded: if denominator ≤ 1e-12, similarity returns 0.0
3. Final score:
   - `0.7 * semantic + 0.3 * lexical`
4. Correctness rule:
   - correct if score `>= 0.58`

Fallback:
- If embedding model is unavailable, grading falls back to lexical score (the semantic component is skipped, not blended with mock data).
- For MCQ mode, grading is deterministic (`A/B/C/D` or exact option text -> correct/incorrect).

### Client-side grading (Web App)

The web app grades entirely in the browser via `gradeQuizLocally()`:

**MCQ Grading:**
1. Letter input (A-D) → index lookup
2. Exact text match → option comparison
3. Fuzzy text match → normalized character overlap with 0.85 threshold
4. Score: 1.0 (correct) or 0.0 (incorrect)

**Open-Answer Grading:**
1. Compute lexical score (same formula as server-side)
2. If browser embeddings are available (via `@huggingface/transformers`):
   - Compute cosine similarity (division-by-zero guarded via epsilon check)
   - Blended score: `0.7 * semantic + 0.3 * lexical`
3. If browser embeddings are unavailable:
   - Use lexical score only
4. Correctness: score ≥ 0.58

**Batch embedding:** All open questions' expected + submitted answers are embedded in a single `embedTextBatch()` call, not per-question.

## Lesson Mode (Optional Path)

Lesson mode is opt-in and does not replace one-off quiz generation.

### Lesson Harness (Preferred)

The lesson harness (`backend/app/services/lesson_harness.py`) generates rich 3-part lessons with embedded quizzes via LLM:

1. **Generation**: `POST /lessons/generate` starts an async job
2. **Planning**: LLM plans 3 parts (foundations, application, mastery)
3. **Content**: LLM generates markdown lesson content + embedded quiz questions per part
4. **Review**: LLM reviews generated content for quality
5. **Storage**: Saved to `AccountStore` with embedded quiz data in checkpoints
6. **Grading**: `POST /lessons/{id}/checkpoints/{index}/grade` grades answers using `grade_lesson_quiz()` from `quiz_service.py`

### Legacy CLI Lesson Mode (Deprecated)

Supported top-level lesson prompts (CLI only):
- `start lesson on <topic>`
- `continue lesson`
- `resume lesson`
- `lesson status`

Flow:
1. User starts lesson on a topic.
2. System creates lesson checkpoints (easy -> medium -> hard) and stores progress in account state.
3. Each checkpoint builds a quiz using existing backend `build_quiz(...)`.
4. **If QA data is missing**, the system auto-generates it from uploaded documents via `CurriculumQAPipeline`, persists to DB (stateless mode) and disk, then builds the quiz.
5. Graded checkpoint result updates:
   - account points/counters
   - lesson attempts
   - checkpoint index (advance only when current checkpoint is passed)
6. On completion, lesson is marked complete and active lesson is cleared.

> **Deprecated**: The CLI lesson commands and `build_checkpoint_quiz` are deprecated. Use the lesson harness for new lessons with embedded quizzes.

Current checkpoint defaults:
- checkpoint 1: easy, 2 questions, pass >= 60%
- checkpoint 2: medium, 3 questions, pass >= 70%
- checkpoint 3: hard, 3 questions, pass >= 75%

## Data Flow

### CLI Flow
1. Generate artifacts:
   - `python -m engine.pipeline.main --input-dir "engine\documents" --run-qa`
2. Launch chatbot CLI:
   - `.\scripts\quiz\run_quiz_chatbot.ps1`
3. User enters prompt:
   - e.g. `give me 5 medium questions on avl trees`
4. System parses intent and builds quiz.
   - Supports exclusion prompts (e.g. `not about trees`).
   - Applies strict exclusion semantics: if exclusion removes all candidates, build fails.
5. TUI collects answers and submits grading.
6. Session artifacts saved:
   - `engine/output/quiz_built.json`
   - `engine/output/quiz_result.json`
   - `engine/output/accounts.json`
   - `engine/output/dashboard_<username>.json`
   - `engine/output/router_telemetry_<username>.json`
   - `engine/output/router_telemetry_history_<username>.json`
   - lesson state inside `accounts.json` under `lesson_progress`
7. Account totals and points are updated from quiz results.
8. Post-result control asks user to choose `continue`, `review`, `dashboard`, or `quit`.

### Web/Chat Flow
1. User sends chat message (e.g. "quiz me on binary trees" or `/lesson quiz`)
2. `chat_tools.try_handle_tool_command()` intercepts before LLM
3. QA loading fallback chain:
   - DB lookup (stateless mode) → disk lookup → auto-generate from docs → error
4. Auto-generated QA is persisted to DB (stateless mode) via `_save_qa_to_db()`
5. `build_quiz()` constructs quiz from QA data
6. Quiz response returned to user with `[QUIZ_DATA_START]...[QUIZ_DATA_END]` tag for browser rendering
7. Browser renders interactive quiz, grades client-side

## Operational Notes

- Embedding model is cached under `data/models/sentence_transformers`.
- `scripts/quiz/run_quiz_chatbot.ps1` calls `python -m engine.quiz.model_setup` first, so model download is enforced once and reused after.
- First setup may download/load model weights; later runs use local cache.
- Intent parsing currently handles common prompt shapes and defaults missing values.
- Last-question UX is constrained: `/submit` to finish, `/quit` to exit; invalid slash commands are not treated as answers.
- Chatbot asks for a username at startup and persists account stats in `accounts.json`.
- Chatbot startup now enforces `create/login` with a numeric PIN (hashed at rest).
- In-quiz router mode is visible per turn:
  - `slash`
  - `tool_call`
  - `fallback_json`
  - local fallback modes
- Router summary is printed at session end and persisted to telemetry files.
- Points model is intentionally simple:
  - `+10` points per correct answer
  - `+5` completion bonus when all questions are answered
- **Auto-QA generation**: When quiz is requested but no QA artifact exists, the system auto-generates QA from uploaded documents. In stateless DB mode (Postgres), the generated artifact is persisted to `AccountArtifactModel` so subsequent API requests find it without re-generation.
- **DB persistence**: `_save_qa_to_db()` uses `hashlib.sha256` for content hashing and `datetime.now(UTC)` for timestamps. Updates existing rows (content + metadata) rather than creating duplicates.

## Extension Path

1. Add semantic rubric checks (key-point coverage) beyond similarity.
2. Expand account progression (streaks, levels, badges, leaderboard).
3. Add API wrapper endpoints for external chatbot function-calling.
4. Add richer intent extraction (time limit, question type, topic disambiguation).
5. Consolidate quiz logic into a shared service module (currently split between `chat_tools.py` and `api/quiz.py`).
