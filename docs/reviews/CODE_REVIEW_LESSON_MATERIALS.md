# Code Review: Save Chat and Quiz to Lessons

Reviewed: **17 July 2026**

Review base: `origin/test-good-try` at `497501d`

Feature branch: `agent/save-chat-quiz-to-lessons`

## Executive summary

This review followed the new feature through the React UI, TypeScript API client, FastAPI authentication and validation layers, account storage, encryption boundary, lesson read model, quiz grading, and CI evidence.

The feature is suitable for review with one deliberate domain decision: a saved Chat or Quiz is a **lesson material**, not a lesson checkpoint. Checkpoints drive grading, completion, and rewards. Appending a Chat transcript to that array would create an ungradable checkpoint and could corrupt progression. The new `materials` collection keeps reference content reusable while preserving the existing lesson state machine.

The implementation adds:

- **Save chat** in the Chat toolbar;
- **Save quiz** before and after quiz grading;
- a destination-Lesson picker with empty, loading, saving, duplicate, and error states;
- an authenticated `POST /lessons/{lesson_id}/materials` endpoint;
- account-scoped encrypted persistence in `lesson_progress.lessons[*].materials`;
- a **Saved from Chat** section in the Lesson reader;
- reusable quiz rendering without a recursive save control;
- request, item-count, and serialized-size limits;
- content-fingerprint idempotence;
- seven automated regression cases and one manual system mission.

No database migration is required. Existing lesson records without `materials` are normalized to an empty list.

## Review scope and method

The review inspected these connected areas:

| Area | Representative files | Question reviewed |
|---|---|---|
| Chat and quiz UI | `ChatView.tsx`, `QuizWidget.tsx`, `SaveToLessonButton.tsx` | Is the action discoverable, reusable, accessible, and failure-aware? |
| Client contract | `frontend/src/api/lessons.ts`, `lessonStore.ts` | Does the browser send and receive one consistent material model? |
| Authentication/API | `backend/app/api/lesson.py`, `api/common.py` | Is the destination account-bound, validated, and safely reported? |
| Persistence/E2EE | `account_system.py`, `storage.py` | Is content encrypted, bounded, backwards-compatible, and isolated from progress? |
| Lesson consumption | `LessonFullScreen.tsx`, `LoginOverlay.tsx` | Can saved content be restored and used after bootstrap? |
| Tests and delivery | `test_lesson_materials.py`, `quality-tests.yml`, `TESTING.md` | Are the important invariants repeatable in CI and honest in documentation? |

The review also ran a broader adjacent Python test selection to identify inherited debt rather than treating the maintained CI subset as the entire repository.

## Architecture and data flow

```text
ChatView / QuizWidget
        |
        | Save chat / Save quiz
        v
SaveToLessonButton ---- GET /lessons (refresh destinations)
        |
        | POST /lessons/{lesson_id}/materials
        v
require_account -> request validation -> content fingerprint
        |
        v
AccountStore.add_lesson_material(account_id, DEK)
        |
        | encrypted lesson_progress.materials persistence
        v
GET /lessons -> storage adapter -> lessonStore
        |
        v
LessonFullScreen -> Markdown transcript or reusable QuizWidget
```

### Data model

Each returned material contains:

| Field | Purpose |
|---|---|
| `material_id` | Stable public identifier derived from content |
| `kind` | Discriminant: `chat` or `quiz` |
| `title` | User-facing label; excluded from duplicate identity |
| `created_at` | Original save timestamp |
| `markdown` | Formatted Chat transcript for `chat` items |
| `quiz` | Structured `QuizBuildResponse` for `quiz` items |

The persisted form additionally carries `source_key`, a SHA-256 content fingerprint. It is intentionally omitted from the public response model.

### API contract

Chat request:

```json
{
  "kind": "chat",
  "title": "Graphs review",
  "messages": [
    {"role": "user", "content": "Explain BFS"},
    {"role": "assistant", "content": "BFS explores level by level..."}
  ]
}
```

Quiz request:

```json
{
  "kind": "quiz",
  "title": "Graphs quiz",
  "quiz": {
    "quiz_id": "quiz_graphs",
    "questions": []
  }
}
```

Success and duplicate response:

```json
{
  "lesson_id": "lesson_graphs",
  "material_id": "material_0123456789abcdef",
  "kind": "chat",
  "created": true,
  "total_materials": 3
}
```

`created: false` means identical content was already present. Duplicate identity ignores the display title so renaming the same source does not bypass idempotence.

## Changes made from review findings

### 1. Progression isolation — resolved

**Risk:** The existing checkpoint array is coupled to current checkpoint index, attempt recording, pass thresholds, XP, and completion. Treating a transcript as a checkpoint would introduce an item with no valid grading path.

**Change:** Added `Lesson.materials` and rendered it separately from checkpoint content. `add_lesson_material()` does not update `current_checkpoint_index`, `completed`, `attempts`, or quiz history.

**Evidence:** `test_store_adds_material_idempotently_without_changing_progress` asserts checkpoint count and index remain unchanged.

### 2. Cross-account material injection — resolved

**Risk:** Accepting a client-supplied account identifier or looking up a Lesson globally could let one user write into another user's study data.

**Change:** The route obtains the authoritative account and DEK from `require_account`; only `lesson_id` comes from the URL. The account store resolves that ID inside the authenticated profile.

**Evidence:** `test_material_api_returns_not_found_for_another_accounts_lesson` verifies a Lesson outside the authenticated account is not a valid destination.

### 3. Duplicate saves and uncontrolled growth — resolved for this feature

**Risk:** Repeated clicks, retries, or network ambiguity could copy the same transcript many times. Large Chat histories could inflate the encrypted account aggregate and increase memory/serialization cost.

**Change:** A deterministic content fingerprint makes writes idempotent. Limits are enforced at both request and persisted aggregate boundaries:

- 50 Chat messages;
- 50,000 total Chat characters;
- 20 Quiz questions;
- 50 saved items per Lesson;
- 2 MB serialized saved materials per Lesson.

**Evidence:** Fingerprint, duplicate, request validation, and 2 MB limit tests run in the maintained API/integration group.

### 4. Hidden Quiz transport leaking into saved Chat — resolved

**Risk:** Chat responses can contain an internal `[QUIZ_DATA_START]... [QUIZ_DATA_END]` payload used to construct an interactive widget. Saving that raw block would expose noisy JSON and duplicate the Quiz visually.

**Change:** The transcript formatter removes the embedded Quiz block from assistant messages while retaining the surrounding explanation.

**Evidence:** `test_chat_material_strips_embedded_quiz_payload` verifies the visible explanation remains and the transport marker is absent.

### 5. Quiz grading failure erased the user flow — improved

**Risk:** `QuizWidget` previously logged a failed grade operation but gave the learner no durable explanation. This is especially costly after answering every question.

**Change:** A failed request returns the widget to the Quiz phase, keeps the answer map, displays a toast, and exposes an inline `role="alert"` retry message.

### 6. Empty destinations and stale Lesson lists — resolved

**Risk:** A picker backed only by the existing Zustand snapshot could show stale destinations or provide a dead-end when no Lesson exists.

**Change:** Opening the picker refreshes Lessons. The empty state explains the prerequisite and links directly to Lessons. Successful saves refresh the store so the open Lesson view can reflect the new material.

### 7. Backwards compatibility — resolved

**Risk:** Older saved lesson records do not contain `materials`.

**Change:** Creation and migration initialize the field; the backend schema, storage adapter, bootstrap mapping, client mapper, and store all default missing values to `[]`.

### 8. FastAPI/pathlib annotation collision — resolved

**Risk:** `backend/app/api/lesson.py` imported both FastAPI's `Path` parameter validator and `pathlib.Path` under the same name. On Python 3.14 with current FastAPI annotation evaluation, importing the router failed when `Path(ge=0)` resolved to `pathlib.Path`.

**Change:** Aliased the FastAPI validator to `PathParam` and retained `Path` for filesystem paths. The checkpoint route now uses `Annotated[int, PathParam(ge=0)]` unambiguously.

## Security and privacy assessment

### Authentication and authorization

- The write endpoint uses `require_account`, not the legacy optional account dependency.
- The unlock token remains bound to its authoritative account and client context upstream.
- The user cannot select an arbitrary account in the request body.
- A missing destination inside the authenticated account returns 404 without exposing another account's metadata.

### Encryption boundary

Saved content is placed under `lesson_progress`, one of the account's private fields. In non-SQLite database mode, the active DEK encrypts this field before `profile_json` is written. The new route explicitly forwards the DEK to the account store. Local SQLite behavior remains consistent with the repository's existing storage policy.

### Input and rendering safety

- Pydantic validates the discriminated request shape and bounded lengths.
- A Chat cannot carry a Quiz payload and a Quiz cannot carry Chat messages.
- Only `user` and `assistant` transcript roles are accepted; system prompts are not saveable through this endpoint.
- Saved Markdown is rendered through the existing `MarkdownRenderer` path rather than injected as raw HTML.
- Material IDs and duplicate keys are server-derived rather than trusted from the browser.

### Privacy caveat

The feature stores the selected Chat transcript in the destination Lesson. The UI labels the action explicitly, but a future privacy improvement should provide a message-range preview before saving and a delete control for individual saved materials.

## Open repository findings

These findings were not broadened into this feature PR unless the change was adjacent and low risk.

### P1 — Account-store locking is instance-local

`AccountStore` creates `self._store_lock` in its constructor, while API helpers commonly create a new `AccountStore` per request. Two request instances therefore do not share the same lock. Both can read the same aggregate, mutate independently, and overwrite one another. This can lose lesson material, attempts, profile edits, or reward updates under concurrency.

**Recommendation:** Use a process-wide lock keyed by account for filesystem mode and transaction/row-level concurrency controls for database mode. Prefer atomic, field-level persistence over read-modify-write of the entire profile.

### P1 — Correct quiz answers are part of the browser DTO

`QuizBuildResponse` includes `expected_answer` and `correct_option_index`, and saved quizzes preserve the same structure. This is acceptable for self-study, where the learner owns the material, but it cannot provide competitive integrity for the planned 1v1 game or any high-stakes mode.

**Recommendation:** Split the contract into public question data and server-only answer keys before shipping competitive Quiz battles. Grade competitive answers exclusively on the server.

### P1 — Private account state is a growing aggregate

Encrypted lesson progress, Quiz history, friends, profiles, and related fields are serialized through an account registry record. The new feature caps its own material collection, but every mutation still rewrites a potentially large encrypted aggregate.

**Recommendation:** Move saved materials and other append-heavy artifacts to first-class account-scoped rows/blobs with independent encryption, quotas, optimistic concurrency, and pagination.

### P2 — Lesson contracts are duplicated across layers

The lesson shape exists as raw dictionaries, Pydantic models, a storage adapter, TypeScript API interfaces, and Zustand state. Adding `materials` required coordinated edits across all of them; a missed default would silently drop restored content.

**Recommendation:** Generate the TypeScript client from FastAPI OpenAPI, introduce typed persistence models, and keep one normalization boundary for legacy records.

### P2 — Core UI components are too large

`LessonFullScreen.tsx` and `QuizWidget.tsx` carry data loading, state transitions, rendering, and mutation behavior in large components. This makes isolated UI regression testing difficult and encourages implicit coupling.

**Recommendation:** Extract `SavedLessonMaterials`, checkpoint navigation, checkpoint quiz, and quiz results into focused components with React Testing Library coverage.

### P2 — API errors are flattened to strings

The generic frontend API client throws stringified response bodies. `SaveToLessonButton` must use a regular expression to recover `detail`, which is brittle and loses HTTP status information.

**Recommendation:** Introduce a typed `ApiError` containing `status`, `detail`, and optional structured validation errors. UI code should branch on typed properties.

### P2 — Historical Python tests have drifted from production symbols

The maintained CI groups pass, but the broader adjacent selection reported five inherited failures:

1. `test_create_lesson_job_rejects_fallback_only_worker_output`;
2. `test_review_regenerates_only_quiz_when_markdown_is_usable`;
3. `test_build_topic_quiz_no_qa_raises`;
4. `test_build_topic_quiz_success`;
5. `test_handle_quiz_uses_quiz_service`.

The Quiz tests patch removed/deprecated symbols (`build_quiz` / `build_topic_quiz`), while the lesson-harness assertions conflict with current fallback acceptance behavior.

**Recommendation:** Decide the intended fallback policy, repair or retire the stale tests, and expose a full-suite baseline job that is allowed to fail temporarily but cannot silently gain new failures.

### P2 — Lesson read-model semantics can mislead the UI

`_lesson_from_account_item()` builds an `objective` string from status metadata and forces `total_checkpoints` to at least one. A Lesson with zero checkpoints can therefore appear to have a phantom checkpoint, and `topic` in the frontend is populated from this synthetic objective.

**Recommendation:** Return distinct `topic`, `objective`, status, and checkpoint-count fields without overloading or coercion.

### P2 — Built template assets can accumulate

The backend template directory contains hashed frontend bundles. Without a controlled clean step, obsolete assets can remain in source or deployment artifacts.

**Recommendation:** Clean only generated manifest-owned assets during build and validate that the committed production bundle matches the frontend source commit.

### P3 — Chat message keys are positional

`ChatView` uses array positions for message rendering in places where stable message IDs are not available. Insertions or retry replacement can cause React to reuse the wrong subtree.

**Recommendation:** Carry stable message IDs through the Chat API and use them as render keys.

## Test and acceptance matrix

| Invariant | Automated evidence | Manual evidence |
|---|---|---|
| Chat and Quiz request shapes cannot be mixed | `test_request_rejects_mismatched_and_oversized_sources` | SYS-14 |
| Hidden Quiz JSON is not displayed as transcript content | `test_chat_material_strips_embedded_quiz_payload` | SYS-14 |
| Identical content is saved once | fingerprint and idempotence tests | SYS-14 repeat-save step |
| Saving does not alter checkpoint progression | store idempotence/progress test | SYS-14 |
| Aggregate material size is bounded | 2 MB store test | — |
| Destination is account-scoped | API account tests | Cross-account exploratory check |
| Saved Quiz remains reusable | TypeScript production build | SYS-14 |
| Older Lessons load with no material field | defaults across schemas/mappers | Existing-account smoke |
| Grading failure retains answers | TypeScript production build | Network interruption during Quiz |

The maintained CI evidence becomes **43 unit + 38 API/integration + 40 Chromium runtime cases**, plus the TypeScript/Vite production build. The 43 unit cases, 38 API/integration cases, and frontend build were run locally for this review; the unchanged Chromium group remains a required pull-request check. Chromium coverage is browser-runtime logic and mocked pipeline coverage, not complete React end-to-end coverage.

## Rollout notes

- No SQL migration is required because the material collection is nested in the existing private lesson progress payload.
- Existing records remain valid and resolve `materials` to an empty list.
- Clients that do not know about `materials` continue to use checkpoints normally.
- The feature should be smoke-tested in both local SQLite and encrypted cloud modes before merge.
- Run SYS-14 in Chrome/Windows and Safari/macOS, including keyboard-only interaction and a narrow viewport.
- For competitive 1v1 quizzes, resolve the public/private Quiz DTO finding before reusing saved Quiz payloads.

## Merge recommendation

**Recommend review and staged merge after CI plus manual SYS-14.** The feature is bounded, backwards-compatible, account-scoped, and covered at its critical persistence invariants. The open P1 items are existing architectural risks that deserve dedicated follow-up issues; the answer-key exposure is a release blocker specifically for competitive Quiz battles, not for the present self-study save-to-Lesson workflow.
