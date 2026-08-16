# Obital — Full-Stack Code Review #3 (Frontend → Backend → Database)

| | |
|---|---|
| **Repository** | `Asdao/Obital` |
| **Commit reviewed** | `211b6b6` (branch `main`) |
| **Prior reviews** | [`CODE_REVIEW.md`](CODE_REVIEW.md) (@ `8bc813e`), [`CODE_REVIEW_2.md`](CODE_REVIEW_2.md) (@ `e63ab08`) |
| **Review date** | 2026-06-12 |
| **Scope** | The **entire stack**, layer by layer: React frontend → backend API → backend services → **database (schema, migrations, transactions)** → content pipeline → cross-cutting security & repo hygiene. This review adds a dedicated **database-layer** pass and a **lesson-subsystem root-cause** analysis not present in earlier reviews, and re-verifies prior findings against current code. |
| **Method** | Parallel deep reads of each layer at HEAD, plus regression checks of every prior finding in changed files. No files were modified. |

> **Reading note.** This document is organized **by layer** so it can be read top-to-bottom as a complete picture of the system. Where a prior finding's status changed, it is marked. Findings are pinned to `211b6b6`. The content pipeline (`engine`) was unchanged since `CODE_REVIEW_2`, so its findings are summarized rather than re-derived.

---

## Table of contents
1. [Executive summary](#1-executive-summary)
2. [Severity overview](#2-severity-overview)
3. [Layer 1 — Frontend (React/Vite)](#3-layer-1--frontend-reactvite)
4. [Layer 2 — Backend API (routing, auth, rate limiting)](#4-layer-2--backend-api)
5. [Layer 3 — Backend services (chat, storage, crypto, sync, usage)](#5-layer-3--backend-services)
6. [Layer 4 — Database (schema, migrations, transactions)](#6-layer-4--database)
7. [Layer 5 — Lesson subsystem (the "disappearing lessons" bug)](#7-layer-5--lesson-subsystem)
8. [Layer 6 — Content pipeline (summary)](#8-layer-6--content-pipeline-summary)
9. [Cross-cutting security posture](#9-cross-cutting-security-posture)
10. [Repository hygiene](#10-repository-hygiene)
11. [What is done well](#11-what-is-done-well)
12. [Prioritized remediation roadmap](#12-prioritized-remediation-roadmap)
13. [Appendix: finding index](#13-appendix-finding-index)

---

## 1. Executive summary

This is the third review of Obital and the first to cover the **full stack end-to-end, including the database layer**. The project continues to improve: the authorization model, the E2EE write discipline, the deployment pipeline, the chat-streaming safety, and the supply-chain hygiene are all in good shape, and several `CODE_REVIEW_2` items were fixed (the bin permanent-delete now guards against deleting live re-uploaded documents).

However, going deeper into the **database** and the **lesson subsystem** surfaced two **new Critical-class issues** that the higher-level reviews could not see:

1. **Schema/migration drift (database).** The two most important tables for the E2EE/sync layer — `account_artifacts` and `account_registry_records` — are **defined in `models.py` but never created by any Alembic migration**. They exist only because `DB_AUTO_CREATE_SCHEMA` defaults on and `Base.metadata.create_all` builds them at boot. On a cloud Postgres deployment that relies on Alembic (auto-create off, the correct production posture), `alembic upgrade head` yields a schema **missing both tables**, and migration `20260612_02` (`ALTER TABLE account_artifacts ADD COLUMN …`) **fails outright**. The canonical schema is currently ambiguous (tables from `create_all`, columns from Alembic).

2. **Lesson data-integrity / "disappearing lessons" root cause (still live).** The recurring lesson-loss bug has a real, identifiable root cause: **all lesson state is persisted via an unsynchronized, whole-profile read-modify-write** in `AccountStore`, mutated concurrently by the request thread, the background lesson-generation daemon, and the sync scheduler — last-writer-wins silently drops lessons/attempts. Compounding it, the lesson-merge fix was applied to only **one** of the two cloud-pull paths; `_pull_registry_from_cloud_sqlite` still overwrites local lessons wholesale, so in SQLite-plus-cloud mode a not-yet-pushed lesson can be erased by the next scheduled pull. The recent commits are well-aimed but **symptomatic** — the concurrency/merge primitive underneath is still missing.

Beyond those, the open items are mostly **High/Medium hardening** carried from prior reviews (the `embeddings.py` silent hash-fallback, the in-memory non-atomic rate limiter, the cross-process usage race, the `/auth/register` error leak, frontend PIN/passphrase-in-store and the login enumeration oracle) plus a handful of **new service-layer findings** (unbounded caches, a blocking `asyncio.run` on a quiz path, a detached background thread that can persist under a stale DEK, plaintext quiz JSON written to server disk).

**Bottom line:** the architecture is sound and the security perimeter is much improved, but **the persistence layer needs attention** — both the schema-vs-migration drift and the lesson concurrency/merge gaps are data-integrity risks that will bite in the cloud/multi-device configuration the product is heading toward. These should be the next focus.

---

## 2. Severity overview

| Severity | Count | Highlights |
|---|---|---|
| **Critical** | 2 | DB schema/migration drift (missing table creates); lesson whole-profile RMW race + unmerged registry pull |
| **High** | 7 | `embeddings.py` silent fallback; naive/aware datetime crash in `list_bin_items`; missing cleanup indexes; `_save` lesson encryption inconsistency; silent decrypt drops lesson store; `/auth/register` leak; frontend PIN/passphrase in store + enumeration oracle |
| **Medium** | ~16 | rate limiter (not thread-safe/per-process); usage cross-process race + unbounded tokens; runtime `ALTER TABLE` vs Alembic; auto-create+Alembic coexistence; unbounded `_qa_cache`; blocking `asyncio.run`/`time.sleep` on request threads; stale-DEK bg thread; plaintext quiz JSON to disk; decrypt passthrough; bin path-collision; DocFullScreen effect race; dead `sseUrl` token-in-URL; streaming disconnect loses usage; lesson lifecycle edge cases |
| **Low** | ~12 | redundant indexes; `account_keys.updated_at` no `onupdate`; error swallowing; dead code; `page-snapshot-1.md` committed; `.gitignore` gaps; fuzzy lesson match |

Trend vs. `CODE_REVIEW_2`: the two prior open **Criticals** (none remained) stay closed; the two **new Criticals** here are deeper-layer issues (DB + lesson concurrency) revealed by going below the API surface.

---

## 3. Layer 1 — Frontend (React/Vite)

The frontend security fixes from `CODE_REVIEW_2` **hold with no regression**:

- ✅ **No XSS sink.** Zero `dangerouslySetInnerHTML` across `frontend/src`; `MessageBubble.tsx:71`, `DocFullScreen.tsx:302`, `LessonFullScreen.tsx:309` all render through `<ReactMarkdown>` with `remarkMath`/`rehypeKatex` (no `rehype-raw`).
- ✅ **Token in `sessionStorage`** (`authStore.ts:34,46,50,54,59`); `localStorage` holds only the non-secret username/theme.
- ✅ **SSE token via `X-Unlock-Token` header** (`useChatStream.ts:75`, `lib/sse.ts:13-18`), not the URL.

**Open / new:**

- **[High] PIN & passphrase retained in the global store.** `authStore.ts:6-7,43-44`; populated by `LoginOverlay.tsx:97,105,118`, cleared only on lock/logout. They're never read elsewhere — keep them in the existing component-local `pinInput`/`passphraseInput` state and never mirror into the global store.
- **[High] Account-enumeration oracle (still present after the recent `LoginOverlay` change).** `LoginOverlay.tsx:48-72`: `handleUsername` probes `login(uname, '')` with an empty PIN and branches on `API 401` (exists) vs `API 404` (not found), reinforced by `apiErrorMessage` (`:14-24`). Reveals which usernames are registered. Return a uniform response for the username step (or drop the pre-probe and always proceed to a unified PIN step).
- **[Medium] Dead `sseUrl()` puts the token in the URL query string.** `api/client.ts:45-53` (`params.set('token', unlockToken)`). Currently unused, but a latent regression — delete it (and the dead `unlock`/`lock` exports in `auth.ts:19-25`).
- **[Medium] `getDoc` effect race.** `DocFullScreen.tsx:35-45` sets `docDetail` from an un-cancelled `getDoc(activeDocId)`; rapidly switching docs can resolve out of order and show stale content. Add an `ignore` flag / `AbortController` in the effect cleanup.
- **[Low] Silent error swallowing.** `DocFullScreen.tsx:43,58-60` and `ChatView.tsx:24,27` `.catch(() => {})` hide load/search failures; surface via the existing `showToast`/`loadError` pattern.
- **[Low] `useChatStream.sendMessage` doesn't abort a prior in-flight stream** (`useChatStream.ts:53-54`) — concurrent sends race on streaming state; abort the previous controller first.
- **[Low] Dead `renderMarkdown` raw-HTML generator** (`lib/markdown.ts:31-60`) — uses `marked.parse` + KaTeX to emit raw HTML; unused and has no sink today, but delete it (or sanitize) so it can't later be wired into an injection.

**Done well:** consistent ReactMarkdown rendering, secrets correctly scoped to `sessionStorage`, header-based auth, and `useChatStream`'s clean timer/teardown and post-abort error suppression (`useChatStream.ts:339-355`).

---

## 4. Layer 2 — Backend API

The token-bound authorization model from `CODE_REVIEW_2` remains solid: `require_account` requires a valid unlock token and binds `account_id` to the token's account (`common.py:73-118`, enforced via `unlock_session.resolve_dek` account match). LLM endpoints are auth'd + rate-limited + input-bounded. CORS is pinned.

**Open / new:**

- **[High] `/auth/register` leaks `str(exc)`.** `auth.py:179-180` returns the raw exception text to the client from a broad `except Exception`. Log it; return a generic "Registration failed".
- **[Medium] Rate limiter is in-memory, per-process, and not thread-safe.** `rate_limit.py:29-41` does prune → read-length → append with no lock (TOCTOU under burst), and `_BUCKETS` is per-worker, so the effective limit is `N × limit` and resets on restart. Add a `threading.Lock`; back it with a shared store (Redis) for multi-worker.
- **[Medium] `RecordTokensRequest.tokens` is unbounded and client-reported.** `schemas.py:193`, `usage.py:48`: a bare `int` with no `ge=0`/upper bound feeds `record_token_usage` — a client can submit negative/huge counts to defeat the quota. `Field(ge=0, le=<max>)`.

**Done well:** `_sse_stream` (`chat.py:79-112`) classifies errors and returns `type(exc).__name__` (not `str(exc)`) with a guaranteed terminal `done`; single unified account-id validator returning 422; pipeline routes genericize errors and log server-side.

---

## 5. Layer 3 — Backend services

**Improved since `CODE_REVIEW_2`:**

- ✅ **Bin permanent-delete no longer erases live re-uploaded docs.** `permanently_delete_expired` (`storage.py:1600`) and `permanent_delete_bin_item` (`storage.py:1641`) now include `AccountArtifactModel.deleted_at.is_not(None)` — the live-doc case (the prior High) is fixed. *Residual (Medium):* matching is still by mutable `relative_path`, so among multiple soft-deleted rows at a colliding path the wrong one can be purged; store the artifact PK in the bin metadata and match on that.

**Open (carried, re-verified at HEAD):**

- **[High] `backend/app/services/embeddings.py` bypasses the shared loader and can silently fall back to hash embeddings.** `embeddings.py:35` constructs `SentenceTransformer(_MODEL_NAME)` with no `cache_folder`/`local_files_only`, and `:36` calls `get_embedding_dimension()` inside a broad `except` (`:38`) that flips `_model_failed=True` on any error — so a dimension/version hiccup downgrades **all** backend RAG to meaningless hash vectors with only a warning. Route it through `shared/embedding_loader.py`; narrow the except so a *loaded* model isn't treated as missing.
- **[Medium] Cross-process / DB usage race + unbounded tokens.** `usage.py:96-116` holds only an in-process lock; `_save_usage` (`:59`) does an unlocked read-modify-write. Use an atomic SQL `UPDATE … SET token_used = token_used + :n` or `SELECT … FOR UPDATE`.
- **[Medium] `crypto_local.decrypt_text` passes plaintext through on read** when the `enc:v1:` prefix is missing (`crypto_local.py:83-90`, now logged). In E2EE/cloud mode this masks rows the migration missed — fail closed when a DEK is present.
- **[Medium] Chat streaming loses usage + assistant message on client disconnect.** `chat_service.py:153-264` records usage (`:252`) and appends the assistant message (`:262`) only after the generator completes; a mid-stream disconnect (GeneratorExit) skips both. Wrap consumption in `try/finally` and persist the partial reply + usage.

**New findings:**

- **[Medium] Unbounded, never-invalidated `_qa_cache`.** `chat_tools.py:44` (`dict[str, CurriculumQAOutput]`) grows for process lifetime and is never invalidated on doc upload/delete, so stale QA persists. Add size/TTL bounds and invalidate `_qa_cache[account_id]` on doc changes. (`storage._text_from_bytes_cache` similarly unbounded — `storage.py:423`.)
- **[Medium] Blocking `asyncio.run` / `time.sleep` on request threads.** `_handle_quiz` calls `asyncio.run(quiz_harness.generate_quiz(...))` (`chat_tools.py:852`) and the lesson-job stream polls with `time.sleep(1.0)` up to 120s (`chat_service.py:443`). The chat endpoints are sync `def`, so each ties up a threadpool worker for the full duration; `asyncio.run` will also raise if ever reached from a running loop. Prefer the existing `_sync_run_async` helper (`chat_tools.py:720`) and bound worker impact.
- **[Medium] Detached QA-generation thread can persist under a stale DEK.** `_try_generate_qa` spawns a daemon thread holding `user_dek` for up to 180s (`chat_tools.py:806`); if the account is locked (DEK rotated, `auth.py:144`) mid-run, it writes a QA artifact (`_save_qa_to_db`, `:795`) under the old key → undecryptable after re-unlock. Re-validate `unlock_version` before persisting, or cancel in-flight generation on lock.
- **[Low] Plaintext quiz JSON written to server disk in stateless/E2EE mode.** `_handle_lesson_quiz`/`_handle_quiz` call `_save_quiz_json` unconditionally (`chat_tools.py:577,879`), leaking quiz content to local disk even in DB/E2EE mode. Gate behind non-DB mode.
- **[Low] `get_doc` spawns an un-deduped hydration thread per cache miss** (`storage.py:808-814`) — repeated reads of the same unhydrated doc start many concurrent threads. Track in-flight hydrations (like `_qa_generating`).

**Done well:** SHA-256 integrity verification on every artifact decrypt (`storage.py:568,830`); the `require_dek` choke-point that *raises* rather than storing plaintext (`crypto_local.py:63-71`, applied at all write sites); `append_message` concurrency handling (Postgres `with_for_update` + retry); the DSML streaming holdback (`chat_engine.py:674-744`); rate-limited, timed Wikipedia calls (`chat_engine.py:39-46,116-118`).

---

## 6. Layer 4 — Database

*This layer was not covered in depth previously; findings here are new.* The migration chain is **linear and consistent** (single head, every revision implements `downgrade()`):

```
20260601_01 init → _02 chat_seq_unique → _03 account_keys
 → 20260602_01 unlock_controls → _02 unlock_sessions → _03 consecutive_lockouts
 → 20260612_01 sync_updated_at → _02 bin_items+artifact_soft_delete → _03 artifact_hydration_status
 → 20260614_01 chat_session_titles (HEAD)
```

**[Critical] `account_artifacts` and `account_registry_records` have no `CREATE TABLE` migration.** They are defined in `models.py:106,120` and used heavily by `storage.py`/`account_sync.py`, but no migration creates them — only `Base.metadata.create_all` (`db.py:135`) does. On an Alembic-managed Postgres deploy with `DB_AUTO_CREATE_SCHEMA=false` (the correct production setting), `alembic upgrade head` produces a schema missing both tables and migration `20260612_02`'s `ALTER TABLE account_artifacts ADD COLUMN …` fails. **Fix:** add a migration that `create_table`s both (composite PK `(account_id, relative_path)` for artifacts, `content` LargeBinary, `sha256`, `size_bytes`, `updated_at`, `deleted_at`, `expires_at`, `hydration_status`) ahead of `20260612_02`, and re-point the down_revision chain.

**[Critical/High — environment-dependent] Plaintext sensitive data to the cloud DB is prevented only by an implicit invariant.** Reward notes/stats and chat title/text are written plaintext when `user_dek is None` (`account_sync.py:417,426,646,658,691`); today those `None`-DEK writers only target the **local** SQLite session (`_local_session`, `:493,:746`), which is intended. But nothing enforces that — a refactor swapping in `_db_session()` (cloud) would silently leak plaintext to Postgres. **Fix:** assert `is_sqlite_mode()` whenever `user_dek is None` in these writers, or make encryption mandatory for any cloud-session write.

**[High] Naive-vs-aware datetime subtraction will crash `list_bin_items`.** All datetime columns are `DateTime(timezone=True)` (`models.py:18-19,…`) but SQLite returns **naive** datetimes; `storage.py:1567` does `(row.expires_at - now)` with `now` aware → `TypeError`. Route every datetime read through the existing `_ensure_aware` helper (`account_sync.py:309`).

**[High] Missing indexes on cleanup/scoping columns** → full scans at scale: `bin_items.expires_at` (`models.py:138`), `account_artifacts.deleted_at`/`expires_at` (`models.py:115-116`), `account_unlock_sessions.expires_at` (`models.py:102`). Add them.

**[High] Runtime `ALTER TABLE` diverges from Alembic.** `_ensure_runtime_schema` (`db.py:141-159`) adds `deleted_at`/`expires_at` as bare `DATETIME` (`:150-152`) — on Postgres that is `timestamp WITHOUT time zone`, contradicting the model's `DateTime(timezone=True)` and the migration's `TIMESTAMPTZ` (`20260612_02:36`). Drop runtime DDL in favor of Alembic, or make it dialect-aware and tz-correct.

**[Medium] `DB_AUTO_CREATE_SCHEMA` + Alembic coexistence is unsafe by default.** Defaults on (`db.py:18`), so `create_all` runs every boot (`:135`). With the missing-table issue above, the schema source is ambiguous; unguarded creates like `account_keys` in `20260601_03` would error if `create_all` already made them. Set `DB_AUTO_CREATE_SCHEMA=false` for Alembic-managed environments and document auto-create as local-SQLite-only.

**[Medium] Long-held cloud transactions span network + crypto.** `_push_artifacts_to_cloud_sqlite` (`account_sync.py:886-981`) holds the cloud session open across per-artifact `encrypt_bytes` and a single terminal commit — contention/timeout risk on the Supabase pooler (NullPool). Batch-commit or encrypt outside the open transaction (as `hydrate_account_artifacts` already does).

**[Low] Redundant indexes** — `ix_chat_messages_session_seq` (`models.py:42`) duplicates the unique constraint's backing index (`:43`), and `ix_chat_messages_session_id` duplicates the composite's leftmost column. **[Low] `account_keys.updated_at` lacks `onupdate`** (`models.py:90`) so lockout writes leave it stale. **[Low] Python-side `default=` vs migration `server_default`** → two clocks for ORM vs raw inserts.

**Done well:** strictly linear migration chain with working downgrades and idempotent column-introspection in later revisions; correct SQLite pragmas via connection event (`WAL`, `foreign_keys=ON`, `busy_timeout=5000`, `db.py:60-66`); dialect-aware locking (`with_for_update` on Postgres, uniqueness+retry on SQLite); precise Supabase transaction-pooler handling (`NullPool` + `prepare_threshold=None`, `db.py:72-78`); the deferred-hydration model with decryption performed outside the DB session.

---

## 7. Layer 5 — Lesson subsystem

The "random lesson disappearance" bug is real, has an identifiable root cause, and is **not fully fixed**.

**Persistence model.** Lesson state lives logically in `profile["lesson_progress"]`, materialized in three physical stores that can drift: the DB registry row (`account_system.py:497-520,622-678`), `accounts.json` (`:614-620`), and the cloud DB. `storage.list_account_lessons` (`storage.py:166-219`) already merges the store view with a *second* raw filesystem read (`_read_filesystem_lesson_progress`, `:222-233`) — evidence the team has hit divergence and is patching around it.

**[Critical] Unsynchronized whole-profile read-modify-write.** `start_lesson` (`account_system.py:142-184`) and every mutator (`apply_lesson_attempt` `:331-382`, `set_active_lesson` `:233-254`, `delete_lesson` `:384-403`, `restart_lesson` `:256-280`) do load-entire-profile → mutate → save-entire-profile with **no lock** anywhere in `AccountStore`. The lesson harness runs generation in a daemon thread and calls `start_lesson` from it (`lesson_harness.py:1626`) while the request thread can call `apply_lesson_attempt`/`set_active_lesson` concurrently → last-writer-wins on the whole profile, silently dropping a lesson/attempt. The atomic `os.replace` (`:691`) prevents torn files, not stale-snapshot clobber. **This is the most plausible root cause.** Fix: a process-level lock around all `AccountStore` mutations (mirroring `lesson_harness._LOCK`), per-field updates instead of whole-profile rewrites, and `SELECT … FOR UPDATE` for the DB path.

**[Critical] DB-mode registry pull overwrites lessons (no merge).** `_pull_registry_from_cloud_sqlite` (`account_sync.py:1523-1561`) writes the cloud `lesson_progress` straight to the local row with **no merge**, unlike the filesystem path `_pull_cloud_to_filesystem` (`:1628-1652`) which merges per-lesson. With pull-then-push on an interval (`sync_scheduler.py:108-109`): create lesson X locally → scheduler pulls a cloud registry lacking X → X is erased before the push runs. The merge fix landed on only one of two pull paths. Fix: extract the merge into a shared helper and use it in both.

**[High] `_save` lesson encryption is inconsistent.** `account_system.py:647-664`: with a DEK, `lesson_progress` is encrypted; without one, it tries to preserve the prior encrypted blob *only if the key is absent* — but `_load` decrypts into a dict, so a later `_save` without a DEK carries a **plaintext dict** and writes plaintext into the column meant to be encrypted. Make encryption state explicit; refuse to save `lesson_progress` as plaintext in Postgres mode.

**[High] Silent decrypt failure drops the whole lesson store.** `_load`/`_load_user` swallow decrypt errors with bare `except: pass` (`account_system.py:509-510,514-516`), leaving `lesson_progress` as an `enc:v1:` string; `get_active_lesson`/`get_lesson_progress`/`list_lessons` then return empty/`None` (`:190-191,323-325,285-287`) — to the user, every lesson "disappeared" when the DEK was merely unavailable in that thread (exactly the background/sync thread context). Distinguish "no lessons" from "could not decrypt" and surface the latter.

**[Medium] Completion clears `active_lesson_id`, stranding final-checkpoint grading.** `apply_lesson_attempt` sets `completed=True` and `active_lesson_id=None` (`account_system.py:372-373`); afterward `get_active_lesson` returns `None`, so `_handle_lesson_quiz` reports "No active lesson" (`chat_tools.py:486-492`) and `grade_checkpoint` (`lesson.py:222-228`) rejects on mismatch. Look lessons up by `lesson_id` directly, not via `active_lesson_id`. (Checkpoint advancement itself is correct — `:365-373`.)

**[Low] String-timestamp comparison** in the lesson merge (`storage.py:195`, `account_sync.py:1645-1647`) works only because formats are identical today; parse to datetime. **[Low] `get_lesson` fuzzy match** (`SequenceMatcher` ≥ 0.65, `storage.py:324`) can resolve to a different lesson — perceived as content "changing." **[Low] `restore_lesson` doesn't reactivate and coerces fields loosely** (`account_system.py:405-434`).

**Verdict:** the recent fixes (filesystem-path merge, the `lesson_progress`-not-a-dict guard, pull-before-push, the dual store+filesystem merge in `list_account_lessons`) are well-aimed but **symptomatic**; the disappearance bug remains live in SQLite+cloud mode (Critical #2) and the underlying RMW race (Critical #1) is unaddressed. **Highest-leverage fixes:** the `AccountStore` lock and porting the merge into `_pull_registry_from_cloud_sqlite`.

**Done well:** atomic file writes (`:685-702`); defensive type guards throughout; correct checkpoint-advance guard; soft-delete-to-bin with TTL for recoverable deletes; quiz-history capped at 500.

---

## 8. Layer 6 — Content pipeline (summary)

Unchanged since `CODE_REVIEW_2`; status carried forward. **Fixed previously:** centralized `shared/embedding_loader.py`, LLM retry/backoff/timeout (`qa_pipeline.py:153-177`), `gather(return_exceptions=True)`, word-boundary chunking, cache key including model + chunk params, prompt-injection instructional defense. **Open:** `embedding_loader.get_embedding_model` is not thread-safe and its `lru_cache` ignores `cache_folder` (Medium) — and note the backend `embeddings.py` service (Layer 3, High) was **not** migrated onto this loader.

---

## 9. Cross-cutting security posture

| Area | Status |
|---|---|
| **AuthN/AuthZ** | ✅ Token-bound `require_account`; account spoofing closed. |
| **E2EE write discipline** | ✅ `require_dek` raises rather than storing plaintext at all user-data write sites. |
| **E2EE read discipline** | 🟡 `decrypt_text` still passes plaintext through on missing prefix (Medium); DB plaintext-to-cloud prevented only by implicit invariant (Layer 4). |
| **LLM endpoints** | ✅ Auth + rate-limited + bounded — but rate limiter not thread-safe/shared (Medium). |
| **CORS** | ✅ Pinned to env origins, credentials disabled. |
| **Secrets handling** | 🟡 Backend solid; frontend still retains PIN/passphrase in store (High). |
| **Information disclosure** | 🟡 Pipeline genericized; `/auth/register` still leaks `str(exc)` (High); login enumeration oracle (High). |
| **Key rotation safety** | 🟡 Detached QA thread can persist under a stale DEK after lock (Medium). |

---

## 10. Repository hygiene

- **[Medium] `page-snapshot-1.md` is committed** (repo root) — a Playwright/accessibility page-tree dump, a debugging artifact, not source. `git rm --cached page-snapshot-1.md`.
- **[Low] `.gitignore` gaps** — no repo-wide `*.log`, `node_modules/` only ignored under `frontend/`, no `page-snapshot*.md`. Add them preventively.
- **Done well:** the prior big offenders stay gone (no model blob, chat logs, `.idea/`, `tsbuildinfo`, `dist/`, `.env`); lean 275-file tree; `.env.example` is the only env file tracked.

---

## 11. What is done well

1. **Token-bound authorization** closing the IDOR (`common.py:73-118`).
2. **E2EE write choke-point** (`require_dek`) plus **SHA-256 integrity verification** on every decrypt.
3. **Migration chain hygiene** — linear, single-head, working downgrades, idempotent later revisions.
4. **Dialect-aware DB engineering** — SQLite WAL/pragmas, Postgres row locks, Supabase pooler handling (`db.py`).
5. **Chat-stream safety** — DSML holdback prevents tool-markup leakage; SSE returns error *types*, not messages.
6. **Bin permanent-delete now guards live docs** (`storage.py:1600,1641`) — a clean fix to a prior High.
7. **Deferred artifact hydration** with off-session decryption — avoids long-held DB connections.
8. **Hardened PIN auth** (lockout, strength validation, forced legacy migration) with a dedicated test suite.
9. **Repo hygiene** broadly maintained; deployment now serves the real React build.

---

## 12. Prioritized remediation roadmap

### P0 — data integrity (do first)
1. **DB Critical** — add `create_table` migrations for `account_artifacts` and `account_registry_records` (before `20260612_02`); set `DB_AUTO_CREATE_SCHEMA=false` for Alembic-managed envs.
2. **Lesson Critical #1** — add a lock around all `AccountStore` mutations; move to per-field / `SELECT … FOR UPDATE` updates.
3. **Lesson Critical #2** — port the lesson-merge into `_pull_registry_from_cloud_sqlite`.
4. **DB H1** — fix the `list_bin_items` naive/aware datetime subtraction (`storage.py:1567`).
5. **DB C2** — assert `is_sqlite_mode()` (or mandate encryption) on any `user_dek is None` cloud-session write.

### P1 — correctness & security
6. **Service H** — route `backend/embeddings.py` through the shared loader; stop the silent hash fallback.
7. **Lesson H** — make `_save` lesson encryption explicit; surface decrypt failures instead of returning empty.
8. **API H** — genericize `/auth/register` errors.
9. **Frontend H** — drop PIN/passphrase from the store; remove the enumeration oracle.
10. **DB H3/H4** — add cleanup indexes; remove divergent runtime `ALTER TABLE`.
11. **Service M** — thread-safe/shared rate limiter; atomic usage increment + bound `tokens`; persist partial stream usage; re-validate DEK before background QA persist.

### P2 — hardening & polish
12. Bound/invalidate `_qa_cache`; avoid blocking `asyncio.run`/`time.sleep` on request threads; dedupe hydration threads; gate plaintext quiz JSON; lesson completion/active-id handling; DocFullScreen effect race; delete dead `sseUrl`/`renderMarkdown`; remove `page-snapshot-1.md` + close `.gitignore` gaps; redundant indexes + `account_keys.updated_at` `onupdate`.

---

## 13. Appendix: finding index

**Critical**
- DB schema/migration drift (missing table creates) — `models.py:106,120`; `backend/alembic/versions/` (none); `db.py:135`
- Lesson whole-profile RMW race — `account_system.py:142-184` + mutators; `lesson_harness.py:1626`
- Unmerged registry pull overwrites lessons — `account_sync.py:1523-1561` (vs `:1628-1652`)

**High**
- `embeddings.py` silent hash fallback — `backend/app/services/embeddings.py:35-38`
- Naive/aware datetime crash — `storage.py:1567`
- Missing cleanup indexes — `models.py:102,115-116,138`
- Runtime `ALTER TABLE` vs Alembic — `db.py:141-159`
- `_save` lesson encryption inconsistency — `account_system.py:647-664`
- Silent decrypt drops lesson store — `account_system.py:509-516,190-191,285-287,323-325`
- `/auth/register` error leak — `auth.py:179-180`
- Frontend PIN/passphrase in store — `authStore.ts:6-7,43-44`
- Login enumeration oracle — `LoginOverlay.tsx:48-72`

**Medium**
- Plaintext-to-cloud invariant fragility — `account_sync.py:417,426,646,658,691`
- Rate limiter not thread-safe/per-process — `rate_limit.py:29-41`
- Usage cross-process race + unbounded tokens — `usage.py:59,96-116`; `schemas.py:193`
- `decrypt_text` plaintext passthrough — `crypto_local.py:83-90`
- Streaming disconnect loses usage/message — `chat_service.py:153-264`
- Unbounded `_qa_cache` — `chat_tools.py:44`; `storage.py:423`
- Blocking `asyncio.run`/`time.sleep` on request threads — `chat_tools.py:852`; `chat_service.py:443`
- Stale-DEK background QA persist — `chat_tools.py:806,795`
- Bin path-collision delete (residual) — `storage.py:1600,1641`
- Auto-create + Alembic coexistence — `db.py:18,135`
- Long-held cloud transaction — `account_sync.py:886-981`
- Lesson completion clears active id — `account_system.py:372-373`; `lesson.py:222-228`
- DocFullScreen effect race — `DocFullScreen.tsx:35-45`
- Dead `sseUrl` token-in-URL — `api/client.ts:45-53`
- Embedding loader thread-safety — `engine/shared/embedding_loader.py:37-43`
- `page-snapshot-1.md` committed — repo root

**Selected Low** — plaintext quiz JSON to disk (`chat_tools.py:577,879`); un-deduped hydration threads (`storage.py:808-814`); redundant chat indexes (`models.py:42`); `account_keys.updated_at` no `onupdate` (`models.py:90`); frontend error swallowing (`DocFullScreen.tsx:43,58-60`, `ChatView.tsx:24,27`); dead `renderMarkdown` (`lib/markdown.ts:31-60`); fuzzy lesson match (`storage.py:324`); `.gitignore` gaps.

---

*Full-stack review complete. No source files were modified. Findings are pinned to commit `211b6b6`. This document complements [`CODE_REVIEW.md`](CODE_REVIEW.md) and [`CODE_REVIEW_2.md`](CODE_REVIEW_2.md); where statuses changed (e.g. bin live-doc deletion now fixed), this review reflects the current code. Because the repository is under active daily development, re-verify line numbers in the lesson/storage/account_system files before acting, as those areas change frequently.*
