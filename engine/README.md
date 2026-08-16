# Engine

This folder isolates implementation for:

1. Step 1: project setup and runtime config loading.
2. Step 2: input text extraction from `.pdf`, `.txt`, or `.md`.
3. Step 3: cache-aware RAG ingestion with parallel embedding workers.
4. Step 4/5: strict curriculum schema validation with retry.
5. Steps 6/7/8: topic extraction, concurrent QA generation, and merged JSON output.

## Files

- `pipeline/`: ingestion + QA generation modules (`config`, `constants`, `extract_text`, `rag_engine`, `qa_pipeline`, `models`, `main`).
- `quiz/`: quiz runtime modules (`model_setup`, `quiz_backend`, `quiz_intent`, `quiz_tui`, `quiz_chatbot_cli`, `account_system`).
- `shared/`: account path resolution, context building, and the embedding loader.

Modules are addressed by their real location — `engine.pipeline.main`,
`engine.quiz.quiz_backend`, and so on. The flat top-level re-export wrappers
that used to shadow them have been removed.

## Technical Design

Architecture and internals are documented in:

- `engine/docs/pipeline/ARCHITECTURE.md`
- `engine/docs/quiz/QUIZ_BACKEND_ARCHITECTURE.md` (quiz build/grade backend on top of offline QA output)

## Required Dependencies

Install in your active virtual environment:

```powershell
pip install --require-hashes -r engine\requirements.txt
```

When updating dependencies, edit `engine/requirements.in` and regenerate the hashed lockfile:

```powershell
python -m piptools compile --generate-hashes --strip-extras --no-annotate --allow-unsafe --resolver=backtracking --output-file engine/requirements.txt engine/requirements.in
```

## Environment Variables

Required:

- `DEEPSEEK_API_KEY`

Optional:

- `DEEPSEEK_MODEL` (default: `deepseek-v4-flash`)
- `PIPELINE_BASE_DIR` (default: current directory)

## Account-Scoped Storage Layout

Pipeline and quiz artifacts are isolated by account id:

- `engine/accounts/<account_id>/documents/`
- `engine/accounts/<account_id>/cache/`
- `engine/accounts/<account_id>/graph_db/`
- `engine/accounts/<account_id>/output/`

Shared account registry for quiz login:

- `engine/accounts/accounts.json`

Storage model note:

- Identity and progress are shared by username in `accounts.json`.
- Runtime artifacts are scoped by `account_id` under `engine/accounts/<account_id>/...`.
- In practice, use the same value for username/account-id when you want a strict 1:1 user workspace.
- Cloud sync mirrors the full account workspace for the selected `account_id`, including `documents`, `output`, `config`, `cache`, and `graph_db`.

## Usage

Run Step 1 + Step 2:

```powershell
python -m engine.pipeline.main --input "C:\path\to\source.pdf"
```

Batch mode from folder (CLI-first):

```powershell
python -m engine.pipeline.main --account-id admin
```

Default behavior for folder mode:

- Put `.pdf` / `.txt` / `.md` files in `engine/accounts/<account_id>/documents`.
- CLI recursively processes every supported file and prints per-file extraction + ingestion outcome.
- Raw extracted text is written to `engine/accounts/<account_id>/output/raw_text/<parent>__<file_stem>.txt`.

Run full backend flow (Step 1/2/3 + Step 6/7/8):

```powershell
python -m engine.pipeline.main --account-id admin --run-qa
```

Custom output path:

```powershell
python -m engine.pipeline.main --account-id admin --input "C:\path\to\source.txt" --output "engine\accounts\admin\output\raw_text.txt"
```

Custom env path:

```powershell
python -m engine.pipeline.main --input "C:\path\to\source.pdf" --env-file ".env"
```

Parallel workers and cache controls:

```powershell
python -m engine.pipeline.main --input "C:\path\to\source.pdf" --workers 8 --force-reingest --purge-missing
```

Tune chunking, embedding model, and backend:

```powershell
python -m engine.pipeline.main --input "C:\path\to\source.pdf" --chunk-size 900 --chunk-overlap 150 --embedding-model all-MiniLM-L6-v2 --backend lightrag
```

## Output

The CLI writes extracted text to:

- `engine/accounts/<account_id>/output/raw_text.txt` (default)

It also prints:

- active model from config
- resolved graph storage directory
- extracted character count
- ingestion cache status (skipped/reason)
- chunk and vector counts
- effective backend used (`jsonl` or `lightrag`; `web` currently falls back unless strict)
- merged QA output path (when `--run-qa` is enabled)
- model activity logs during QA generation (`[MODEL] ...`, `[PROGRESS] ...`)
- token usage summary (actual API usage when available, estimate fallback otherwise)

## Notes

- Supported input types: `.pdf`, `.txt`, `.md`.
- `.pdf` extraction uses `pypdf.PdfReader`.
- Text normalization trims lines and removes empty lines.
- Ingestion cache file: `engine/accounts/<account_id>/cache/ingestion_manifest.json`.
- Ingestion cache now tracks `backend` and `chunk_hashes` per file for safe skip decisions.
- Vector index file: `engine/accounts/<account_id>/graph_db/vector_index.jsonl`.
- LightRAG sidecar mapping file: `engine/accounts/<account_id>/graph_db/lightrag_doc_map.json`.
- If `sentence-transformers` is unavailable at runtime, a deterministic fallback embedder is used so pipeline execution still succeeds.
- Runtime cache/output files are ignored by git and recreated automatically.
- Add/change file extensions and default values in `constants.py`.
- For execution flow, cache semantics, and module contracts, use `docs/pipeline/ARCHITECTURE.md`.

## Quick Variable Guide

- `--workers`: number of parallel embedding workers. Increase for throughput, reduce if CPU contention.
- `--account-id`: required account scope for documents/cache/vectors/outputs (default: `default`).
- `--chunk-size`: larger chunks reduce vector count but widen semantic scope.
- `--chunk-overlap`: overlap between chunks. Increase to preserve boundary context.
- `--embedding-model`: local model id for sentence-transformers.
- `--backend`: `jsonl`, `lightrag`, or reserved `web` placeholder.
- `--backend-strict`: fail fast if requested backend cannot initialize (no fallback).
- `--include`: repeatable glob include filter for discovered files.
- `--exclude`: repeatable glob exclude filter for discovered files.
- `--max-documents`: cap selected files after filtering.
- `--dry-run`: show selected files/settings without extraction/ingestion/QA.
- `--qa-concurrency`: concurrent topic QA requests (Step 7).
- `--qa-per-topic`: QA pairs per topic.
- `--max-global-context-chunks`: retrieval breadth for curriculum generation.
- `--max-topic-context-chunks`: retrieval breadth per topic QA request.
- `--retrieval-mode`: `keyword`, `vector`, or `hybrid` for topic context selection.
- `--retrieval-top-k`: number of top-ranked chunks used for topic context.
- `--retrieval-alpha`: hybrid weight for vector score (0..1).
- `--max-topics`: hard cap on generated curriculum topics.
- `--qa-output`: output path for merged `CurriculumQAOutput` JSON.
- `--changed-only-qa`: skip QA when corpus hash and settings are unchanged.
- `--force-qa`: force QA run even with `--changed-only-qa`.
- `--schema-retry-limit`: retries for strict Step 4/5 schema correction.
- `--cheap-mode`: low-cost preset for QA generation.

Token usage metadata:

- QA output metadata includes `token_usage`:
  - `api_calls`
  - `prompt_tokens`, `completion_tokens`, `total_tokens` (actual when provider returns usage)
  - `estimated_prompt_tokens`, `estimated_completion_tokens` (fallback estimates)

## Command Reference

Install dependencies:

```powershell
pip install --require-hashes -r engine\requirements.txt
```

Run one file (Step 1/2/3):

```powershell
python -m engine.pipeline.main --input "C:\path\to\source.pdf"
```

Run folder mode (Step 1/2/3):

```powershell
python -m engine.pipeline.main --account-id admin
```

Preview selection only (no token spend):

```powershell
python -m engine.pipeline.main --account-id admin --dry-run
```

Filter documents and limit count:

```powershell
python -m engine.pipeline.main --account-id admin --include "CS20404S/*.md" --exclude "*MidTerm*" --max-documents 8
```

Run full backend from folder (Step 1/2/3 + Step 4/5 + Step 6/7/8):

```powershell
python -m engine.pipeline.main --account-id admin --run-qa
```

Skip QA if corpus/settings unchanged:

```powershell
python -m engine.pipeline.main --account-id admin --run-qa --changed-only-qa
```

Force QA run even when unchanged:

```powershell
python -m engine.pipeline.main --account-id admin --run-qa --changed-only-qa --force-qa
```

Run full backend from a single file:

```powershell
python -m engine.pipeline.main --input "C:\path\to\source.pdf" --run-qa
```

Force re-ingest and purge missing cached files:

```powershell
python -m engine.pipeline.main --account-id admin --force-reingest --purge-missing
```

Tune embedding ingestion:

```powershell
python -m engine.pipeline.main --account-id admin --workers 8 --chunk-size 900 --chunk-overlap 150 --embedding-model all-MiniLM-L6-v2 --backend lightrag
```

Tune QA generation and schema retries:

```powershell
python -m engine.pipeline.main --account-id admin --run-qa --qa-concurrency 6 --qa-per-topic 5 --max-global-context-chunks 24 --max-topic-context-chunks 10 --schema-retry-limit 3
```

Vector retrieval mode:

```powershell
python -m engine.pipeline.main --account-id admin --run-qa --retrieval-mode vector --retrieval-top-k 8
```

Hybrid retrieval mode (recommended):

```powershell
python -m engine.pipeline.main --account-id admin --run-qa --retrieval-mode hybrid --retrieval-top-k 8 --retrieval-alpha 0.8
```

Low-cost run (token saver preset):

```powershell
python -m engine.pipeline.main --account-id admin --run-qa --cheap-mode
```

Cheap mode defaults:

- `--max-topics 4`
- `--qa-per-topic 2`
- `--qa-concurrency 2`
- `--max-global-context-chunks 6`
- `--max-topic-context-chunks 3`

Retrieval defaults:

- `--retrieval-mode hybrid`
- `--retrieval-top-k 8`
- `--retrieval-alpha 0.8`

Override any cheap-mode default explicitly:

```powershell
python -m engine.pipeline.main --account-id admin --run-qa --cheap-mode --max-topics 6 --qa-per-topic 3
```

Write merged output to a custom file:

```powershell
python -m engine.pipeline.main --account-id admin --run-qa --qa-output "engine\accounts\admin\output\my_curriculum_qa.json"
```

## Quiz Backend (Terminal First)

After generating `output_curriculum_qa.json`, you can build and grade quizzes without rerunning DeepSeek calls.
Current grading uses a blended score:
- semantic similarity via sentence embeddings (`all-MiniLM-L6-v2`)
- lexical overlap fallback/assist (precision/recall/F1 blend)
- correctness threshold: `0.58`

Build quiz:

```powershell
python -m engine.quiz.quiz_backend --qa-output engine\accounts\admin\output\output_curriculum_qa.json build-quiz --request-file engine\accounts\admin\output\quiz_build_request.json
```

Grade quiz:

```powershell
python -m engine.quiz.quiz_backend --qa-output engine\accounts\admin\output\output_curriculum_qa.json grade-quiz --request-file engine\accounts\admin\output\quiz_grade_request.json --quiz-file engine\accounts\admin\output\quiz_built.json
```

See `engine/docs/quiz/QUIZ_BACKEND_ARCHITECTURE.md` for data contracts and architecture details.

Chatbot-style terminal flow (prompt -> build quiz -> answer -> grade):

```powershell
.\scripts\quiz\run_quiz_chatbot.ps1
```

Note: startup now preloads the embedding model into chatbot memory, and grading runs with local-cache-only model loading.
At quiz chatbot startup, user must `create` or `login` with username + PIN; account points are tracked locally.

Example prompt:

```text
give me 5 medium questions on avl trees
```

Lesson mode prompts (optional flow, separate from one-off quiz):

```text
start lesson on trees
give me a lesson on heaps
continue lesson
lesson status
lessons
resume 1
restart lesson 1
```

> **Deprecated**: The CLI lesson commands are deprecated. Use the lesson harness via `POST /lessons/generate` for new lessons with embedded quizzes.

Notes:
- `resume <n>` is the primary way to open/start a listed lesson checkpoint.
- `open lesson <n|lesson_id>` is still accepted as a backward-compat alias.
- `start lesson` without a topic is rejected; use `start lesson on <topic>`.
- Lessons without embedded quizzes can no longer build checkpoint quizzes from the QA pool; regenerate them through the backend lesson harness.

Quiz mode prompt examples:

```text
give me 5 multiple choice questions on b trees
give me 3 open ended questions on avl trees
```

Quiz session commands:

- `/next`
- `/back`
- `/skip`
- `/explain`
- `/submit`
- `/quit`

Quiz UX rules:

- On the last question, use `/submit` to finish or `/quit` to exit.
- Unknown slash commands are rejected and are not saved as answers.
- After results, the bot asks `continue` or `quit` before starting another quiz.
- At chatbot top-level, slash commands like `/submit` are ignored unless a quiz is active.
- After each submitted quiz, points are awarded and account totals are shown.

Points model:

- `+10` per correct answer
- `+5` completion bonus when all quiz questions are answered

Account auth:

- `create`: new username + 4-12 digit PIN
- `login`: existing username + PIN
- PIN is stored as PBKDF2-SHA256 hash (with per-user salt) in local account storage
- Legacy accounts are auto-normalized to lowercase usernames when loaded

Saved outputs:

- `engine/accounts/<account_id>/output/quiz_built.json`
- `engine/accounts/<account_id>/output/quiz_result.json`
- `engine/accounts/<account_id>/output/router_telemetry_<username>.json`
- `engine/accounts/<account_id>/output/router_telemetry_history_<username>.json`
- shared account registry: `engine/accounts/accounts.json`
- lesson progress under `lesson_progress` in `accounts.json`
