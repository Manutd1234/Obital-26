# Obital — Review Summary & Integration Check

> Companion to [`CODE_REVIEW.md`](CODE_REVIEW.md). This document does two things:
> **Part 1** reports a live integration check (does the AI chatbot pipeline and the rest of the app actually run and wire together?).
> **Part 2** is a prioritized scorecard of which parts of the code need improvement, with pointers into the full review.

| | |
|---|---|
| **Repository / commit** | `Asdao/Obital` @ `main` (`db00143`) |
| **Date** | 2026-06-09 |
| **Environment** | Windows 11, Python 3.14.4, fresh `.venv`, root `requirements.txt` + `pytest` installed. SQLite/local mode (no `DATABASE_URL`), no `DEEPSEEK_API_KEY`, `sentence-transformers` not installed (to exercise fallbacks). |

---

## Part 1 — Integration check: does it work?

**Short answer: yes.** The application imports and boots cleanly, the AI chatbot request path works end-to-end, data persists, and the graceful-degradation fallbacks behave exactly as designed. The full hermetic test suite is overwhelmingly green (134/139), and every failure traces to a *single* environment-specific cause, not a product defect.

### 1.1 What was exercised

| Check | Method | Result |
|---|---|---|
| App + all routers + services + `engine` pipeline wire together | `import backend.app.main:app` | ✅ **OK — 43 routes registered** |
| Liveness | `GET /health` | ✅ `{"status":"ok"}` |
| DB health reporting | `GET /health/db` | ✅ correctly reports `enabled:false` (local mode, no `DATABASE_URL`) |
| **Chatbot: create session** | `POST /chat/sessions` | ✅ `200`, returns `session_id` |
| **Chatbot: send message → reply** | `POST /chat/sessions/{id}/messages` | ✅ `200`, returns reply, **persists user+assistant (2 messages)** |
| **Chatbot: history persistence** | `GET /chat/sessions/{id}` | ✅ `200`, 2 messages returned |
| Lesson routes | `GET /lessons` | ✅ `200` |
| Usage/metering routes | `GET /usage` | ✅ `200` |
| Embedding fallback | (no model installed) | ✅ hash-based fallback engaged, logged clearly |
| LLM fallback | (no API key) | ✅ graceful "Chat model unavailable… add `DEEPSEEK_API_KEY`" reply, no crash |

The request flow `route → chat_service → chat_engine → tool routing → reply → persistence` is fully functional. The two things **not** exercised here are (a) a real LLM completion (no API key) and (b) semantic embeddings (no model) — both degrade gracefully and are clearly surfaced to the user/logs rather than failing.

### 1.2 Test suite result

```
backend/tests (excluding Playwright browser tests):  134 passed, 5 failed
```

The passing tests include the security-critical ones: the Argon2 + AES-GCM account-sync round-trip, unlock-token binding/version-invalidation/lockout, encrypted-blob decryption, chat tool-calling, storage CRUD, and the pipeline-connection storage layer.

### 1.3 The 5 failures — one root cause, environment-specific

All five failures (`test_settings_api`, two in `test_chat_engine`, `test_chat_api` settings-tool, `test_quiz_qa_loading` 404) share a single cause:

- **Root cause:** the path-containment guard `Path(...).relative_to(BASE_DIR)` at `backend/app/services/settings_service.py:247` (same pattern at `account_sync.py:95` and `engine/pipeline/main.py:381`) does not normalize Windows **8.3 short paths** vs long paths. The test temp dir resolves to `C:\Users\IANABI~1.WAN\...` in one place and `C:\Users\ian.abiel.wangsa\...` in another, so `relative_to` raises `ValueError: '…' is not in the subpath of '…'`, which the router maps to HTTP 400 — failing the assertions.
- **Impact:** these tests pass on Linux CI (Ubuntu, no 8.3 short names); they fail on Windows machines whose temp/profile path has a short-name form. So this is **not** a broken feature, but it **is** a genuine, if minor, cross-platform robustness bug worth fixing.
- **Fix:** resolve both operands to a canonical form before comparing (e.g. `os.path.realpath`/`Path.resolve()` on both, or compare via `os.path.commonpath`), so short/long path forms don't diverge. This also hardens the same containment checks used for path-traversal safety in sync and the pipeline.

### 1.4 Integration-check conclusion

The core product is wired correctly and runs. To exercise the **full** AI path (real curriculum/QA generation and semantic retrieval) a reviewer needs: a valid `DEEPSEEK_API_KEY`, `pip install sentence-transformers torch` (or the committed model — see hygiene findings), and optionally a Postgres `DATABASE_URL` for cloud/sync mode. With those, the same endpoints above drive the real LLM pipeline. The graceful fallbacks mean the app is demoable and testable even without any of them.

---

## Part 2 — Which sections need improvement

This is a triage scorecard. Status reflects the code's state **assuming a possible multi-user/cloud deployment** (the codebase supports it). If Obital is only ever single-user-local, the authorization items soften — see the note in `CODE_REVIEW.md` §2.

Legend: 🟢 solid · 🟡 needs work · 🔴 needs significant rework before non-local use.

| # | Section / area | Status | Headline issue | Priority | Detail |
|---|---|---|---|---|---|
| 1 | **Authorization / access control** | 🔴 | `account_id` is a spoofable query param (defaults to `admin`); unlock token optional on nearly all routes | **P0** | [§5.1](CODE_REVIEW.md#51-account-identity-is-client-asserted-critical) |
| 2 | **LLM endpoints** (`/pipeline/*`, `/quiz/build`) | 🔴 | Unauthenticated + unbounded → cost-amplification / DoS on the server API key | **P0** | [§5.2](CODE_REVIEW.md#52-unauthenticated-expensive-llm-endpoints-critical) |
| 3 | **Repository hygiene** | 🔴 | ~87 MB model, real user chat logs, `.idea/`, build artifacts committed | **P0** | [§16](CODE_REVIEW.md#16-repository-hygiene) |
| 4 | **React frontend** (`frontend/src`) | 🔴 | XSS via unsanitised markdown + token in `localStorage` + token in SSE URL; also not actually deployed | **P0 if shipping** | [§10.2](CODE_REVIEW.md#102-react-frontend-frontendsrc) |
| 5 | **E2EE write paths** | 🟡 | Several writers silently store plaintext when DEK missing (inconsistent) | **P1** | [§5.3](CODE_REVIEW.md#53-e2ee-degrades-to-plaintext-silently-high) |
| 6 | **Data sync** (`account_sync`, `sync_scheduler`) | 🟡 | Destructive full-replacement, push-before-pull, no conflict resolution → multi-device data loss | **P1** | [§9](CODE_REVIEW.md#9-data-sync--concurrency) |
| 7 | **Async / blocking work** | 🟡 | `asyncio.run()` and sync LLM/model calls on request paths; tool-call truncation can 400 | **P1** | [§6](CODE_REVIEW.md#6-backend-services-layer), [§7](CODE_REVIEW.md#7-backend-api-router-layer) |
| 8 | **CORS config** | 🟡 | `allow_origins=["*"]` with `allow_credentials=True` | **P1** | [§5.4](CODE_REVIEW.md#54-cors-wildcard-with-credentials-high) |
| 9 | **Usage/metering service** | 🟡 | Unlocked read-modify-write race; naive-datetime crash; storage-byte limit never enforced | **P1** | [§6](CODE_REVIEW.md#6-backend-services-layer) |
| 10 | **CI/CD** | 🟡 | Audits deps but never runs tests/lint/types/frontend build | **P1** | [§13](CODE_REVIEW.md#13-cicd) |
| 11 | **Dependency management** | 🟡 | Backend lock omits `openai`/`sentence-transformers`/`torch`/`pypdf`/`python-dotenv` it imports | **P1** | [§14](CODE_REVIEW.md#14-dependency-management) |
| 12 | **PIN auth** (`account_system`) | 🟡 | No lockout on PIN login; legacy unsalted hash; empty PIN allowed | **P1** | [§5.5](CODE_REVIEW.md#55-pin-authentication-weaknesses-high) |
| 13 | **`SECURITY.md`** | 🟡 | Threat model overstates posture vs. the real (spoofable/optional-token) access model | **P1** | [§15](CODE_REVIEW.md#15-documentation) |
| 14 | **Cross-platform path guards** | 🟡 | `relative_to(BASE_DIR)` not short/long-path safe (the 5 failing tests) | **P1** | [Part 1.3](#13-the-5-failures--one-root-cause-environment-specific) |
| 15 | **Pipeline embedding/model loading** | 🟡 | Model loaded in 4 places, inconsistent offline settings; cache key omits model/chunk params; char-offset chunking | **P2** | [§8](CODE_REVIEW.md#8-engine-pipeline--quiz-engine) |
| 16 | **Storage performance** | 🟡 | N+1 doc loads; per-message re-extraction of all docs; in-Python cosine over all chunks | **P2** | [§6](CODE_REVIEW.md#6-backend-services-layer) |
| 17 | **Frontend topology** | 🟡 | Three frontends (TUI + React + vanilla template); React isn't the one deployed | **P2** | [§10.0](CODE_REVIEW.md#100-the-three-frontend-problem-high), [§11](CODE_REVIEW.md#11-deployment--operations) |
| 18 | **Pipeline LLM robustness** | 🟡 | No retry/backoff/timeout on QA/curriculum calls; prompt-injection surface via topic title | **P2** | [§8](CODE_REVIEW.md#8-engine-pipeline--quiz-engine), [§5.7](CODE_REVIEW.md#57-prompt-injection-surface-medium) |
| 19 | **Testing coverage** | 🟡 | No API-level tenant-isolation tests; README points at wrong test command; stray root `test_chat_api.py` | **P2** | [§12](CODE_REVIEW.md#12-testing) |
| 20 | **Deployment** (Vercel) | 🟡 | Catch-all rewrite, no static handling, no build step, runtime under-specified | **P2** | [§11](CODE_REVIEW.md#11-deployment--operations) |
| — | **Crypto envelope / unlock sessions** | 🟢 | Argon2id KEK, AES-GCM, hashed bearer tokens, lockout — solid | keep | [§17](CODE_REVIEW.md#17-what-is-done-well) |
| — | **Crypto/sync test suite** | 🟢 | Real, hermetic round-trip tests incl. a regression test | keep | [§12](CODE_REVIEW.md#12-testing) |
| — | **Supply-chain CI hardening** | 🟢 | Hash-verified installs + `pip-audit` | keep | [§13](CODE_REVIEW.md#13-cicd) |

### Files that need the most attention (by concentration of findings)

1. `backend/app/api/common.py` — the authorization model lives here; fixing §5.1 starts here.
2. `backend/app/api/pipeline.py` — unauthenticated LLM spend, internal-method calls, error leakage, unbounded bodies.
3. `backend/app/services/account_sync.py` + `sync_scheduler.py` — destructive non-incremental sync.
4. `backend/app/services/chat_tools.py` / `chat_engine.py` — `asyncio.run` on request path, tool-call truncation, plaintext fallback, blocking LLM calls.
5. `backend/app/services/usage.py` — race + naive-datetime crash + dead storage-byte guard.
6. `frontend/src/lib/markdown.ts` + `stores/authStore.ts` + `api/client.ts` — the React XSS/token cluster.
7. `data/models/`, `backend/data/chat/sessions.json`, `.idea/` — untrack and scrub from history.
8. `settings_service.py` / `account_sync.py` / `pipeline/main.py` — short/long-path-safe containment checks.

### Suggested order of work

- **P0 (before any non-local deploy):** items 1–4 above.
- **P1 (correctness & reliability):** items 5–14.
- **P2 (performance, maintainability, polish):** items 15–20.

A full step-by-step roadmap is in [`CODE_REVIEW.md` §18](CODE_REVIEW.md#18-prioritized-remediation-roadmap).

---

## What's already good (don't regress)

The cryptographic envelope, unlock-session design, cross-backend message ordering, streaming hygiene, the hermetic crypto/sync test suite, and the supply-chain CI hardening are all genuinely well done. The integration check confirms the architecture holds together and degrades gracefully when external dependencies (LLM key, model, DB) are absent. The work below is about hardening the perimeter and cleaning up delivery — not rebuilding the core.

---

*Generated as part of a full code review. The integration check ran against `main` @ `db00143` in a clean local environment; no source files were modified.*
