# Obital — Code Review #2 (Follow-up Re-Review)

| | |
|---|---|
| **Repository** | `Asdao/Obital` |
| **Commit reviewed** | `e63ab08` (branch `main`) |
| **Previous review baseline** | `8bc813e` (see [`CODE_REVIEW.md`](CODE_REVIEW.md)) |
| **Review date** | 2026-06-12 |
| **Scope** | Full repository, with emphasis on (a) verifying which findings from `CODE_REVIEW.md` were addressed, and (b) reviewing new code: the **bin / soft-delete** feature, the **plaintext→encrypted migration**, the **rate limiter**, the **centralized embedding loader**, the **token-bound authorization** rewrite, and the **React/Vite deployment switch**. |
| **Method** | Re-read of all changed subsystems plus targeted regression checks of every prior finding. No files were modified. |

> **Note on a fast-moving target.** During this review the repository advanced ~40 commits across two same-day fetches. Backend and pipeline files cited here were **unchanged between `002fffc` and `e63ab08`**, so those findings reflect HEAD. The **frontend** changed heavily in that window, so all frontend findings below were **re-verified directly against `e63ab08`**.

---

## Table of contents
1. [Executive summary](#1-executive-summary)
2. [Status of prior findings (regression table)](#2-status-of-prior-findings)
3. [Open & new findings by severity](#3-open--new-findings-by-severity)
4. [Review of the new features](#4-review-of-the-new-features)
5. [What is now done well](#5-what-is-now-done-well)
6. [Prioritized remediation roadmap (current state)](#6-prioritized-remediation-roadmap)
7. [Appendix: finding index](#7-appendix-finding-index)

---

## 1. Executive summary

This is a **strong, substantive response** to the first review. The team closed the most serious problems and the codebase is materially safer and cleaner than at `8bc813e`.

**The headline: there are no open Critical findings at `e63ab08`.** Every Critical from `CODE_REVIEW.md` has been resolved:

- **Authorization IDOR (was the #1 Critical) is fixed.** A new `require_account` dependency requires a valid unlock token and binds `account_id` to the token's account; all account-scoped routers use it.
- **Unauthenticated LLM cost/DoS endpoints are fixed** — `/pipeline/*` and `/quiz/build` now require auth, are rate-limited, and bound their inputs.
- **Repository hygiene is fixed** — the ~87 MB model, the committed user chat logs, `.idea/`, the TS build cache, and `stitch_assets` are all untracked.
- **The frontend XSS is fixed** — `MessageBubble` switched to `react-markdown` (no `dangerouslySetInnerHTML` remains anywhere in `frontend/src`).
- **The unlock token left `localStorage`** (now `sessionStorage`) and the **SSE token moved out of the URL** into a header.

Many High/Medium items were also fixed: the `asyncio.run()` request-path crash, the `usage` naive-datetime crash, the destructive non-incremental sync (now pull-before-push with timestamp-merged upserts), the settings ciphertext-leak, the storage N+1 + per-message re-extraction, the tool-call truncation 400, the silent plaintext-on-missing-DEK writes (now centralized behind `require_dek` which raises), the "model loaded 4×" duplication (now a shared `embedding_loader`), the missing LLM retry/backoff/timeout, and the PIN-auth weaknesses (lockout, strength validation, forced legacy migration, with a new test file).

**What remains.** No Criticals, but a cluster of **High** items — most of them *introduced by the new code*:

1. The **bin permanent-delete** matches underlying artifacts by a mutable `relative_path`, so it can delete a *re-uploaded live* document (no `deleted_at` guard).
2. The **plaintext→encrypted migration** decides what to re-encrypt solely by the `enc:v1:` prefix, and `encrypt_bytes` has **no** version prefix — risking double-encryption and leaving pre-existing artifact ciphertext undetectable/unmigrated.
3. A **restore-vs-cleanup race** on bin rows (no row lock).
4. The **chat-session merge** unions messages by positional count, which can drop/duplicate messages under genuine concurrent edits.
5. **`backend/app/services/embeddings.py`** still bypasses the new shared loader and may silently fall back to hash embeddings (verify the sentence-transformers dimension call) — degrading RAG quality.
6. Two frontend holdovers: **PIN/passphrase retained in the global store**, and the **account-enumeration oracle** in the login flow.

Plus Medium items around the rate limiter (in-memory, per-process, not thread-safe), a residual cross-process usage race, unbounded self-reported token usage, and several bin/migration edge cases.

**Approximate counts at `e63ab08`:** Critical: 0 · High: 8 · Medium: ~14 · Low: ~10. The trajectory is clearly positive; the remaining work is mostly hardening the *new* features to the same standard as the fixes.

---

## 2. Status of prior findings

Legend: ✅ Fixed · 🟡 Partial · 🔴 Still open

| Prior finding (`CODE_REVIEW.md`) | Status | Evidence at `e63ab08` |
|---|:---:|---|
| **C** Spoofable `account_id`, optional token (IDOR) | ✅ | `require_account` requires token + binds account (`common.py:73-118`); `resolve_dek` enforces `row.account_id == account_id` (`unlock_session.py:93`). All account routers updated. |
| **C** Unauthenticated LLM endpoints (`/pipeline/*`, `/quiz/build`) | ✅ | `require_account` + `rate_limit(...)` + input caps (`pipeline.py:152,188,231,94-110`; `quiz.py:97-98`). |
| **C** ~87 MB model + chat logs + `.idea` + artifacts committed | ✅ | All untracked (`data/models`, `backend/data/chat/sessions.json`, `.idea`, `tsconfig.tsbuildinfo`, `stitch_assets` removed). |
| **C** React XSS via unsanitised markdown | ✅ | `MessageBubble.tsx:71` uses `react-markdown` (remark-math/rehype-katex only); **0** `dangerouslySetInnerHTML` in `frontend/src`. |
| **C** Unlock token in `localStorage` | ✅ | Now `sessionStorage` (`authStore.ts:34,46,50,54,59`); `localStorage` only holds the non-secret username. |
| **C** SSE token in URL query string | ✅ | Sent as `X-Unlock-Token` header (`useChatStream.ts`, `sse.ts`; backend `common.py:75-76`). Dead `sseUrl()` remains (Low). |
| **C** Vercel served wrong/stale UI; no build | 🟡 | Real Vite build now served (`index.html` → `/assets/index-*.js`; `main.py:101-104` mounts `StaticFiles`). Residual: `vercel.json` catch-all proxies assets through Python; no SPA fallback. |
| **H** Default `account_id="admin"` | 🟡 | Removed from enforced path (`require_account` uses `Query(...)`); still lingers in `valid_account_id`/`optional_user_dek` and some `storage.py` service signatures. |
| **H** Two inconsistent account-id validators | ✅ | Single source: `_validate_account_id_format` → `normalize_account_id` (`common.py:31-35`). |
| **H** `POST /usage/tokens` unauthenticated | ✅ | Now `require_account_id` (`usage.py:40`). (New Medium: value unbounded + self-reported.) |
| **H/M** Raw exception strings leaked | 🟡 | Pipeline genericized + logged (`pipeline.py:181-182,224-225,268-272`); `auth.py:180` register still returns `str(exc)`. |
| **H** CORS `*` + credentials | ✅ | Pinned to `CORS_ORIGINS` env, `allow_credentials=False` (`main.py:74-86`). |
| **H** `asyncio.run()` on request path | ✅ | Offloaded to a daemon thread with loop-aware executor (`chat_tools.py:627-638,662,713`). |
| **H** `usage.get_usage` naive-datetime crash + RMW race | 🟡 | Datetime coerced to UTC (`usage.py:29-30`); in-process `_usage_lock` added — but cross-process/DB-row race remains. |
| **H** Destructive non-incremental sync | ✅ | Pull-before-push (`sync_scheduler.py:108-109`); timestamp-gated incremental upsert + merge (`account_sync.py:277-408`). (New: positional message merge — see H4.) |
| **H** Tool-call truncation → 400 | ✅ | `executed_calls`/`tool_results` built in lockstep; break before append (`chat_engine.py:491-543,740-793`). |
| **H** Embedding model loaded 4× / offline ignored | ✅ | Central `shared/embedding_loader.py` (lru_cache, cache_folder, local_files_only); all pipeline consumers use it. (Backend `embeddings.py` excepted — see H5.) |
| **H** PIN: no lockout, weak/empty PIN, legacy unsalted hash | ✅ | Lockout w/ backoff (`account_system.py:797-826`), digit/length validation (`52-54`), forced legacy migration on login (`91-97`), new tests. (Residual: fail-open on bad timestamp, legacy still *accepted* until migrated — see M.) |
| **M** Settings decrypt-failure returned ciphertext as prompt | ✅ | Falls back to `DEFAULT_SYSTEM_PROMPT`, sha256-verified legacy path (`settings_service.py:125-151,211-225`). |
| **M** storage N+1 + per-message re-extraction | ✅ | Load-all-docs once (`storage.py:674-684`) + (path,mtime,size)/(name,sha256) caches (`320-337`). |
| **M** Embedding dim hardcoded 384 | 🟡 | Now dynamic on success (`embeddings.py:36`) — but the call may return `None`/wrong method → silent hash fallback (H5). |
| **M** No retry/backoff/timeout on pipeline LLM calls | ✅ | `_create_with_retry` w/ backoff + `timeout` (`qa_pipeline.py:153-177`); `gather(return_exceptions=True)`. |
| **M** Prompt-injection via topic title/id | 🟡 | Instructional defense preamble + post-gen identity overwrite (`qa_pipeline.py:182-186,301-305,344-346`); no structural escaping (acceptable). |
| **M** Char-offset chunking; cache key omits model/params | ✅ | Word-boundary snapping (`rag_engine.py:247-255`); cache key includes model + chunk size/overlap (`99-108,125-129`). |
| **H** Plaintext-on-missing-DEK silent writes | ✅ | Centralized `require_dek(...)` raises (`crypto_local.py:63-71`); applied at all four user-data write sites (`storage.py:711,735,872,1025,443-474`). (Read-side passthrough remains — M.) |
| **H** PIN/passphrase retained in frontend store | 🔴 | Still in zustand state (`authStore.ts:6-7,32-33,43-44`), cleared only on lock/logout. |
| **H** Account-enumeration oracle (login) | 🔴 | Empty-PIN probe + `API 401`/`404` string branch persists (`LoginOverlay.tsx:39-48`). |

---

## 3. Open & new findings by severity

### 🔴 Critical
**None open at `e63ab08`.**

### 🟠 High

**H1 — Bin permanent-delete can erase a re-uploaded *live* document (path-based match).**
`storage.py:1331-1346` (and `permanent_delete_bin_item`, `1373-1387`) delete the underlying document by matching `AccountArtifactModel.relative_path == relative_path` taken from the bin metadata, **with no `deleted_at IS NOT NULL` guard**. Because `add_doc` de-duplicates names by suffixing and a path is reused after a doc is binned, a new upload can take the same `documents/<name>` path; expiry/permanent-delete then deletes the **live** re-uploaded doc.
**Fix:** scope the delete with `AccountArtifactModel.deleted_at.is_not(None)`, or match on a stable unique id rather than the mutable path.

**H2 — Plaintext→encrypted migration risks double-encryption; `encrypt_bytes` has no version prefix.**
`migrate_plaintext_rows.py:139,172` re-encrypt any text row lacking the `enc:v1:` prefix — the prefix is the *only* signal. Any non-prefixed ciphertext (legacy output, partial migration) would be wrapped twice and become undecryptable. Separately, `crypto_local.py:79-80` `encrypt_bytes` emits **no** version header, which is exactly why `account_artifacts.content` (documents/embeddings) **cannot be detected** by the migration (acknowledged in the script docstring) — pre-enforcement artifact plaintext is invisible and unmigrated.
**Fix:** before re-encrypting, attempt `decrypt_text(dek, value)` and only encrypt if it isn't already valid ciphertext; add a versioned prefix/AAD header to `encrypt_bytes` and a one-time audited artifact migration. Add a `--dry-run` and require a backup (the script overwrites in place with no snapshot).

**H3 — Bin restore races the cleanup daemon (no row lock).**
`restore_doc` (`storage.py:1238-1269`) and `permanently_delete_expired` (`storage.py:1321-1360`, run hourly by `bin_cleanup.py`) operate on the same rows with no `with_for_update()` and no re-check of `expires_at` inside the transaction. A restore in flight as `expires_at` passes can be clobbered by the daemon.
**Fix:** lock the bin row in both paths and re-check `expires_at > now()` within the restore transaction.

**H4 — Chat-session merge unions messages by positional count → concurrent-edit data loss.**
`_merge_chat_sessions` (`account_sync.py:298-307`) keeps `base` and appends `other_msgs[len(base_msgs):]` by index; `_write_chat_sessions_to_db` keys new messages by positional `seq_no` (`356-389`). If two devices appended *different* messages to the same session, the shorter side's unique messages are dropped and seq numbers can collide.
**Fix:** merge by stable message identity (e.g. `created_at` + `role` + content hash), not positional count.

**H5 — `backend/app/services/embeddings.py` bypasses the shared loader and may silently disable the real model.**
This service was **not** migrated to `shared/embedding_loader.py`: it constructs `SentenceTransformer(_MODEL_NAME)` with no `cache_folder`/`local_files_only` (`embeddings.py:35`), so it can hit the network and fail offline. It also calls `_model.get_embedding_dimension()` (`embeddings.py:36`) inside a broad `except Exception`; depending on the installed sentence-transformers version this method may be absent (the documented API is `get_sentence_embedding_dimension()`) or return `None`, in which case the exception is swallowed and the service **silently falls back to hash embeddings even when the model loaded** — degrading all RAG/semantic search via the backend path.
**Fix:** route this service through `embedding_loader`, capture the dimension defensively (guard `None`), and narrow/log the except so a loaded-model error isn't misread as "not installed." *(Verify against the pinned sentence-transformers version.)*

**H6 — PIN/passphrase retained in the frontend global store.** *(prior finding, still open)*
`authStore.ts:6-7,32-33,43-44` keep `pin` and `passphrase` in session-wide state, cleared only on lock/logout. They aren't needed after the unlock token is obtained.
**Fix:** keep them in local component state during login and discard immediately after `unlock()`.

**H7 — Account-enumeration oracle in the login flow.** *(prior finding, still open)*
`LoginOverlay.tsx:39-48` probes with `login(uname, '')` and branches on `API 401` (exists) vs `API 404` (doesn't), surfaced in the UI. This reveals which usernames exist.
**Fix:** return a uniform response for the username step; decide register-vs-login after PIN entry, or have the backend not distinguish 401/404 for this probe.

**H8 — `/auth/register` leaks raw exception text.** *(prior finding, partial)*
`auth.py:180` returns `HTTPException(detail=str(exc))` from a broad `except Exception`, potentially exposing internal store errors.
**Fix:** log `.exception(...)` and return a generic message (as the pipeline routes now do).

### 🟡 Medium

- **M1 — Rate limiter is in-memory, per-process, and not thread-safe.** `rate_limit.py:17,29-41`: a module-level dict with no lock and read-prune-check-append; with N workers the effective limit is N× and it resets on restart, and concurrent requests for one key can exceed the limit. **Fix:** shared store (Redis) for multi-worker, and a `threading.Lock` around the check.
- **M2 — Cross-process / DB usage race remains.** `usage.py` `_usage_lock` is in-process only; `_save_usage` does an unlocked read-modify-write of `profile_json` per call (`59-84`). **Fix:** atomic `UPDATE ... SET token_used = token_used + :n` or `SELECT ... FOR UPDATE`.
- **M3 — `RecordTokensRequest.tokens` unbounded and self-reported.** `schemas.py:174-175`, `usage.py:43`: no `ge`/`le`; a client can report negative/huge values (cross-account abuse is closed since it's token-scoped now). **Fix:** `Field(ge=0, le=<max>)`.
- **M4 — `decrypt_text` passes plaintext through on read.** `crypto_local.py:83-90`: returns the value unchanged when the `enc:v1:` prefix is absent, masking any rows the migration missed. Acceptable for SQLite mode; risky in cloud mode. **Fix:** in non-SQLite mode treat a missing prefix as an error.
- **M5 — Bin local-mode permanent-delete unlinks an unscoped filesystem path.** `storage.py:1347-1353,1388-1394` unlink `meta["relative_path"|"original_path"]` (an absolute path) with no check that it's within the account trash dir — an arbitrary-file-delete primitive if metadata is corrupted. **Fix:** validate the resolved path is under the account's `.trash`.
- **M6 — `list_bin_items` returns already-expired rows.** `storage.py:1296-1318` doesn't filter `expires_at >= now`; users can try to restore items the daemon is about to delete. **Fix:** filter expired rows; reject restore of expired items.
- **M7 — Binned lesson `state` stored plaintext in `bin_items.metadata_json`.** `bin.py:57,66`; the `bin_items` table isn't in the migration's encrypted set. Inconsistent with E2EE for lesson content on the cloud DB. **Fix:** encrypt the lesson state blob or document the exception.
- **M8 — PIN lockout fails open on a malformed `pin_locked_until`.** `account_system.py:797-806` swallows parse errors and proceeds to verification — corruption/tampering bypasses the lock. **Fix:** fail closed (treat unparseable as locked) with a logged warning.
- **M9 — PIN failure counter is a non-atomic read-modify-write.** `account_system.py:86-101`: concurrent attempts can clobber `failed_pin_attempts` (file) or race without a row lock (DB), diluting throttling. **Fix:** increment under a transaction/lock.
- **M10 — `embedding_loader.get_embedding_model` is not thread-safe and ignores `cache_folder` in its cache key.** `embedding_loader.py:37-43` used across a thread pool (`rag_engine.py:188-191`) can trigger concurrent heavyweight loads; `lru_cache` keyed only on `model_name`. **Fix:** double-checked `Lock` (as `backend/embeddings.py` does); include `cache_folder` in the key.
- **M11 — Streaming usage/message lost on client disconnect.** `chat_service.py:148`: `record_token_usage` and the assistant `append_message` run only after the generator is fully consumed; a mid-stream disconnect under-counts usage and drops the partial reply. **Fix:** persist partial usage/reply in a `try/finally`.
- **M12 — Reward push from files can drop cloud-only events.** `account_sync.py:614-658` writes from local files with delete-then-reinsert and no cloud-side union (the SQLite merge path mitigates, the file path doesn't). **Fix:** union events against existing row events at write time.
- **M13 — Vercel proxies all static assets through the Python function; no SPA fallback.** `vercel.json:2-4` catch-all → `api/index.py`; assets are served by FastAPI `StaticFiles` rather than the edge (perf/cost), and only `/` is handled so any future client route 404s on refresh. **Fix:** serve `/assets/*` as static at the edge; add an SPA fallback if routing is added.
- **M14 — DocFullScreen fetch race / useChatStream concurrent-send.** *(frontend heavily reworked at `e63ab08`; re-verify)* The doc-detail effect lacked an abort/ignore guard and `useChatStream` overwrote the abort controller on a second send. These files changed substantially in the latest batch — confirm against current code before fixing.

### 🟢 Low (selected)
- Dead `optional_user_dek` retains the old optional-token pattern; remove it (`common.py:139-176`).
- `="admin"` defaults remain in some `storage.py` service signatures (`1238,1296,1363`) — drop so a missing arg fails loudly.
- Dead `sseUrl()` with `?token=` in `client.ts` — delete so it can't be reintroduced.
- `_text_from_bytes_cache` is an unbounded dict (`storage.py:326`) — use an LRU/size cap.
- Temp-file leak on extraction if killed mid-write (`storage.py:303-317`).
- No confirmation dialog on permanent bin delete (`BinFullScreen.tsx`).
- Migration imports unused symbols; profile sub-field `json.dumps` round-trip must match the read path.
- Lesson restore deletes the bin row before the external write is durable (`bin.py:65-80`) — a crash duplicates state.

---

## 4. Review of the new features

### 4.1 Token-bound authorization (`common.py`, `rate_limit.py`)
**Solid.** `require_account` + `resolve_dek`'s `account_id` match closes the IDOR cleanly, and it's applied consistently across routers. The rate limiter is the right idea but needs a shared, thread-safe backing store for production (M1). Remove the now-dead `optional_user_dek` to prevent regression.

### 4.2 Bin / soft-delete (`bin.py`, `bin_cleanup.py`, `storage.py`, `BinFullScreen.tsx`)
**Good design** — soft-delete is a metadata flag (`deleted_at`/`expires_at`), so encrypted content is never decrypted/rewritten and account-scoping/E2EE survive binning; the cleanup daemon is properly guarded. **But** the permanent-delete path matches artifacts by mutable path (H1), races restore (H3), can unlink unscoped local paths (M5), lists expired items (M6), and stores lesson state in plaintext (M7). These are all fixable without redesign.

### 4.3 Plaintext→encrypted migration (`scripts/migrate_plaintext_rows.py`)
**Correct in the common case** (idempotent via prefix skip; correctly no-ops on SQLite; honest about limits) but carries real data-integrity risk: re-encryption keyed only on the prefix (H2), no `--dry-run`/backup, and per-row failures aren't surfaced in the error count. The deeper gap is that `encrypt_bytes` has no version header, so document/embedding artifact plaintext is undetectable — the migration genuinely cannot complete E2EE for those columns until that's added.

### 4.4 Centralized embedding loader (`shared/embedding_loader.py`)
**Resolves the "loaded 4×" finding** for the pipeline/quiz consumers with a cached, offline-aware loader. Needs thread-safety (M10), and the **backend** `embeddings.py` service was left out of the centralization and may silently degrade to hash embeddings (H5).

### 4.5 React/Vite deployment switch
**Confirmed real and a genuine improvement** — the 3,230-line hand-written template is gone; the built bundle is served via `StaticFiles`. Residual deploy-config gaps are Medium (M13).

---

## 5. What is now done well

1. **Token-bound auth** (`common.py:73-118`, `unlock_session.py:93`) — the single most important fix; closes horizontal privilege escalation.
2. **Defense-in-depth on LLM endpoints** — auth + rate limit + input caps + timeouts (`pipeline.py`, `quiz.py`).
3. **E2EE write discipline** — one `require_dek` choke-point that *raises* rather than silently storing plaintext (`crypto_local.py:63-71`), applied at every user-data write site.
4. **Incremental, conflict-aware, pull-before-push sync** with timezone-safe comparisons (`account_sync.py:268-408`, `sync_scheduler.py:108-109`).
5. **Correct tool-call history construction** and **DSML streaming holdback** (`chat_engine.py:491-543,664-678`).
6. **Hardened PIN auth** with lockout, strength validation, forced legacy migration, atomic 0600 writes, and a dedicated test suite (`account_system.py:52-54,797-826,633-644`; `tests/test_account_system.py`).
7. **Centralized, offline-aware embedding loader** (`shared/embedding_loader.py`) and **LLM retry/backoff/timeout** (`qa_pipeline.py:153-177`).
8. **Real React build deployed** with `StaticFiles` and a `react-markdown` render path that eliminates the XSS sink.
9. **Repository hygiene** fully addressed (model, chat logs, IDE config, build artifacts, design assets all untracked).
10. **CORS pinned**, **single account-id validator**, **pipeline error genericization** — a broad sweep of the smaller findings.

---

## 6. Prioritized remediation roadmap

### P0 — data-integrity & E2EE correctness (do first)
1. **H1** — add `deleted_at IS NOT NULL` (or stable-id) guard to bin permanent-delete so it can't erase live re-uploaded docs.
2. **H2** — make the migration test-decrypt before re-encrypting; add a version header to `encrypt_bytes`; add `--dry-run` + backup; then run the artifact migration.
3. **H3** — row-lock + re-check `expires_at` in restore vs. cleanup.
4. **H4** — merge chat messages by stable identity, not positional count.

### P1 — security & correctness
5. **H5** — route `backend/embeddings.py` through the shared loader; fix the dimension call so a loaded model isn't silently downgraded.
6. **H6 / H7 / H8** — drop PIN/passphrase from the store; remove the enumeration oracle; genericize `/auth/register` errors.
7. **M1 / M2 / M3** — shared+locked rate limiter; atomic usage increment; bound `tokens`.
8. **M4 / M5 / M8 / M9** — strict decrypt in cloud mode; scope bin local unlink; fail-closed PIN lockout; atomic PIN counter.

### P2 — hardening & polish
9. **M6 / M7 / M10 / M11 / M12 / M13** — filter expired bin items; encrypt bin lesson state; thread-safe embedder; persist partial stream usage; union reward events on push; edge static assets + SPA fallback.
10. **Low** — remove dead `optional_user_dek`/`sseUrl()`/`="admin"` defaults; LRU the byte-cache; confirm dialog on permanent delete.

---

## 7. Appendix: finding index

**High**
- H1 Bin permanent-delete by mutable path — `storage.py:1331-1346,1373-1387`
- H2 Migration double-encrypt + `encrypt_bytes` no prefix — `migrate_plaintext_rows.py:139,172`; `crypto_local.py:79-80`
- H3 Restore vs cleanup race — `storage.py:1238-1269` vs `1321-1360`; `bin_cleanup.py`
- H4 Chat merge positional data loss — `account_sync.py:298-307,356-389`
- H5 Backend embeddings bypasses loader / silent fallback — `backend/app/services/embeddings.py:35-36`
- H6 PIN/passphrase in store — `frontend/src/stores/authStore.ts:6-7,43-44`
- H7 Enumeration oracle — `frontend/src/components/auth/LoginOverlay.tsx:39-48`
- H8 `/auth/register` error leak — `backend/app/api/auth.py:180`

**Medium**
- M1 Rate limiter in-mem/not-threadsafe/per-process — `backend/app/api/rate_limit.py:17,29-41`
- M2 Cross-process usage race — `backend/app/services/usage.py:59-84`
- M3 Unbounded self-reported tokens — `backend/app/schemas.py:174-175`; `usage.py:43`
- M4 `decrypt_text` plaintext passthrough — `crypto_local.py:83-90`
- M5 Bin local unscoped unlink — `storage.py:1347-1353,1388-1394`
- M6 Bin lists expired items — `storage.py:1296-1318`
- M7 Bin lesson state plaintext — `bin.py:57,66`
- M8 PIN lockout fail-open — `account_system.py:797-806`
- M9 PIN counter non-atomic RMW — `account_system.py:86-101`
- M10 Embedding loader thread-safety / cache key — `shared/embedding_loader.py:37-43`
- M11 Streaming usage/message lost on disconnect — `chat_service.py:148`
- M12 Reward push drops cloud-only events — `account_sync.py:614-658`
- M13 Vercel asset proxy / no SPA fallback — `vercel.json:2-4`; `main.py:107`
- M14 Frontend fetch/send races (re-verify) — `DocFullScreen.tsx`, `useChatStream.ts`

**Selected Low** — dead `optional_user_dek` (`common.py:139-176`); `="admin"` service defaults (`storage.py:1238,1296,1363`); dead `sseUrl()` (`client.ts`); unbounded `_text_from_bytes_cache` (`storage.py:326`); temp-file leak (`storage.py:303-317`); no confirm on permanent delete (`BinFullScreen.tsx`); lesson restore ordering (`bin.py:65-80`).

---

*Follow-up review complete. No source files were modified. This document complements — and where noted, supersedes the status of — [`CODE_REVIEW.md`](CODE_REVIEW.md). Because the repository is under active daily development, findings are pinned to commit `e63ab08`; backend/pipeline citations were unchanged since `002fffc`, and all frontend findings were re-verified at `e63ab08`.*
