# Backend Tests

## Running Tests

```powershell
# Maintained unit evidence group (44 cases)
python -m pytest engine/tests/test_quiz_modes.py engine/tests/test_checkpoint_normalization.py backend/tests/test_auth_api.py backend/tests/test_stateless_storage.py -q

# Maintained API/integration evidence group (40 cases)
python -m pytest backend/tests/test_sync_api.py backend/tests/test_workspace_api.py backend/tests/test_stream_and_routes.py backend/tests/test_lesson_materials.py -q

# Run browser automation tests (requires Chromium + pytest-playwright)
pip install -r backend/tests/requirements-test.in
playwright install chromium
python -m pytest backend/tests/test_browser_js.py -q

# Historical full suite (includes known legacy baseline failures)
python -m pytest backend/tests/ -v
```

See [`../../docs/testing/TESTING.md`](../../docs/testing/TESTING.md) for named cases, expected assertions, regression mapping, verified results, and the distinction between maintained evidence groups and historical test debt. The maintained groups run in [Core Quality CI](../../.github/workflows/quality-tests.yml).

## Test Files

### API & Service Tests (no browser required)

| File | Covers |
|------|--------|
| `test_settings_api.py` | GET/PATCH `/settings` prompt CRUD, empty prompt rejection |
| `test_chat_api.py` | Chat message send, empty message 400, unknown lesson 404 |
| `test_auth_api.py` | Unlock token binding, version invalidation, rate limiting, missing client ID |
| `test_sync_api.py` | Sync push/pull token validation, DB mode enforcement, lockout |
| `test_rewards_api.py` | Dashboard payload, reward sessions, encrypted notes at rest |
| `test_api_validation.py` | Input validation, lesson NL lookup, DB health endpoint |
| `test_chat_engine.py` | System prompt resolution chain, settings tool-calling |
| `test_quiz_qa_loading.py` | QA load from disk/DB, encrypted blob decryption, 404 handling |
| `test_embeddings_storage.py` | Document upload → embedding save → vector verification with E2EE |
| `test_pipeline_connection.py` | Storage layer: lesson/doc CRUD, NL lookup |
| `test_stateless_storage.py` | Cloud DB mode: settings, accounts, encrypted doc upload/search |
| `test_account_sync.py` | Push/pull full account state round-trip with Argon2 + AES-GCM |
| `test_lesson_materials.py` | Save Chat/Quiz to Lessons: validation, redaction, idempotence, size limits, account scope |

### Browser Automation Tests (Playwright)

| File | Covers |
|------|--------|
| `test_browser_js.py` | Client-side JS: grading, cosine similarity, pipeline orchestration |

## Browser Test Architecture

Browser tests use **Playwright** to run JavaScript functions inside a real Chromium browser. There are two modes:

1. **Blank page** (`page` fixture): Used for testing pure JS functions in isolation. No app loaded — avoids interference from the Web TUI's boot sequence.

2. **Live app** (`app_page` fixture): Navigates to the FastAPI server. Used for pipeline integration tests with `page.route()` to mock API responses.

### What Gets Tested

**Pure functions** (injected via IIFE into blank page):
- `tokenize` — alphanumeric token extraction
- `editDistance` — Levenshtein distance
- `isTypoMatch` — fuzzy token matching with 0.85 threshold
- `fuzzyOverlap` — set intersection + typo matching
- `lexicalScore` — `0.7*F1 + 0.3*recall` scoring
- `cosineSimilarity` — vector similarity with epsilon guard
- `mcqGrading` — letter/text/fuzzy MCQ matching
- `gradeQuizLocally` — orchestrator with optional mock embeddings

**Integration** (against live server):
- Pipeline 3-step flow: curriculum → topic-qa → save (mocked API)
- Curriculum failure stops the pipeline
- Topic QA failure stops the pipeline

### Test Fixtures

Defined in `conftest.py`:

- `browser_ctx` — session-scoped Playwright Chromium browser
- `app_url` — session-scoped uvicorn server on random port
- `page` — per-test blank page for pure JS tests
- `app_page` — per-test page navigated to the live app

### Dependencies

Playwright and pytest-playwright are **not** in the main lockfiles. They are dev-only dependencies for browser tests:

```
backend/tests/requirements-test.in
```

Install with:
```powershell
pip install -r backend/tests/requirements-test.in
playwright install chromium
```

### How JS Functions Are Tested

The grading reference functions are injected into Chromium via `page.evaluate()`. This avoids application boot interference and verifies JavaScript numeric/string behavior in a real browser runtime.

These injected functions are maintained test reference implementations, not automatically imported React component code. Therefore the suite is described as **browser-runtime logic coverage**, not complete React visual E2E coverage. The pipeline cases do navigate the live FastAPI-served application and intercept HTTP calls, but visual onboarding, responsive layout, hover states, and real provider behavior remain manual-system missions.
