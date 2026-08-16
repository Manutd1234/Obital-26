# Security Policy

Obital is a personal study companion that handles encrypted user data. This document describes the security architecture, threat model, and reporting process.

## Supported Versions

| Version | Supported |
|---------|-----------|
| Current (main branch) | Yes |

## End-to-End Encryption (E2EE)

All user content is encrypted at rest using a per-account envelope encryption scheme:

```
User Passphrase
    │
    ▼
Argon2id key derivation (time_cost=3, memory_cost=65536, parallelism=2)
    │
    ▼
KEK (Key Encryption Key)
    │
    ├── wraps ──► DEK (Data Encryption Key) via AES-GCM
    │
    ▼
DEK encrypts private user data:
  - chat messages
  - documents and embeddings
  - quiz history, active quizzes, and lesson progress
  - memory profiles, system prompts, and settings
  - QA artifacts
```

### What is encrypted

- Chat message text
- Uploaded documents and their embeddings
- Quiz history, active quiz state, and lesson progress records
- User-customized system prompts, settings, and memory profiles
- Friend lists
- QA generation artifacts

### Write-Site Enforcement (docs/reviews/CODE_REVIEW.md §5.3)

Every write-site that stores user data in non-SQLite mode **must** have access to
the DEK. The helper ``require_dek(dek, context)`` enforces this invariant:

```python
stored = encrypt_text(require_dek(user_dek, "chat message"), msg.text)
```

If ``user_dek`` is ``None``, a ``ValueError`` is raised immediately — encryption is
never silently skipped.  Nine writer locations were hardened in June 2026:

| Location | Behavior |
|----------|----------|
| ``chat_tools._save_qa_to_db`` | Unconditional ``require_dek`` |
| ``pipeline._upsert_artifact`` | Unconditional ``require_dek`` |
| ``account_sync._write_chat_sessions_to_db`` | Unconditional ``require_dek`` |
| ``account_sync._write_reward_sessions_from_files`` | Unconditional ``require_dek`` |
| ``account_sync._write_reward_sessions_to_db`` | Conditional on ``is_sqlite_mode()`` |
| ``storage.append_message`` | Conditional on ``is_sqlite_mode()`` |
| ``storage.append_reward_session_log`` | Conditional on ``is_sqlite_mode()`` |
| ``account_system._save`` | Warns if DEK absent; only encrypts when present (shared code path with usage tracking) |
| ``account_system._load`` seeding fallback | Warns if DEK absent; only encrypts when present (startup path) |

SQLite / local mode is exempt by design (plaintext storage is intentional).

**Migration window.**  Before enforcement, ``decrypt_text(dek, value)`` silently
returned plaintext when the value lacked the ``enc:v1:`` prefix (a convenience
for transition).  It now logs a ``WARNING`` on each passthrough.  After all
existing rows have been re-encrypted (see ``scripts/migrate_plaintext_rows.py``),
this passthrough should be tightened to raise an error.

### What is stored in plaintext

- Account IDs and PIN hashes (for authentication)
- Unlock session token hashes (not the tokens themselves)
- File metadata (paths, sizes, timestamps)
- Leaderboard aggregates (points, quiz counts, accuracy inputs)
- Presence status and last-seen timestamps
- SQLite database file structure (table names, column names, non-encrypted fields)

### Storage Modes

| Mode | Database | Encryption |
|------|----------|------------|
| **SQLite local** | `LOCAL_DATABASE_URL` → `backend/data/obital.db` | Plaintext locally; encrypted only during cloud sync |
| **Postgres cloud** | `DATABASE_URL` → Supabase/Neon/RDS | Application-level E2EE (AES-256-GCM) on every write |
| **SQLite + cloud sync** | Local SQLite + remote Postgres | Plaintext in SQLite; encrypted when synced to cloud |

In SQLite mode, data is stored as plaintext in the local database. Encryption only happens during cloud sync — the sync layer encrypts data before writing to the cloud and decrypts when pulling back to local.

## Authentication

### PIN Authentication

- PBKDF2-HMAC-SHA256 with 200,000 iterations and per-user random salt
- Constant-time comparison via `hmac.compare_digest` to prevent timing attacks
- Used for login identity verification only (does not unlock encryption)

### Passphrase Unlock

- Argon2id derivation from user passphrase (GPU/ASIC-resistant)
- Unwraps the Data Encryption Key (DEK) for encryption/decryption
- Session tokens are random (`secrets.token_urlsafe(32)`) and only their SHA-256 hash is stored
- Tokens are bound to `X-Client-Id` header and per-account unlock version
- Tokens are held in-memory only and never persisted to disk

### Brute-Force Protection

- 5 failed unlock attempts triggers temporary lockout
- Exponential backoff doubles each lockout period (max 240 minutes)
- Successful unlock resets the failure counter

## Threat Model

### In scope

- Offline-first: system functions without network access (SQLite mode)
- Cloud mode: database compromise does not reveal plaintext data (application-level E2EE)
- SQLite mode: local database is plaintext on disk; encryption only applies during cloud sync (see Storage Modes table above)
- Session hijacking: tokens are bound to client context
- Brute-force: lockout and backoff protect unlock passphrases
- Background sync: only syncs accounts with active unlock sessions

### Out of scope

- Physical access to the user's machine
- Compromise of the LLM API provider (DeepSeek)
- Side-channel attacks on the local machine

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public GitHub issue
2. Email security concerns to the repository maintainer
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact assessment
4. You should receive a response within 7 days

## Security Tooling

- Dependencies are hash-locked via `pip-compile --generate-hashes`
- CI runs `pip-audit` against pinned dependency sets
- The engine audit currently ignores `GHSA-rrmf-rvhw-rf47` / `CVE-2025-3000`
  for `torch==2.12.0` because no official stable fixed PyTorch release is
  published yet. The affected API is legacy `torch.jit.script`; project code
  should not use TorchScript and should prefer `torch.compile()` for local model
  compilation. Remove the audit exception once PyTorch publishes a fixed stable
  release.
- The npm audit gate is `scripts/check_npm_audit.py`. `npm audit` has no
  equivalent of pip-audit's `--ignore-vuln`, so an advisory with no published
  fix would leave the job permanently red and stop the gate carrying
  information. The script fails on any high or critical advisory except those
  listed in `ALLOWED_ADVISORIES` with a reason and a removal condition. It
  currently allows one: `GHSA-f88m-g3jw-g9cj`, sharp's inherited libvips CVEs.
  sharp is a Node-only native addon that `@huggingface/transformers` declares
  for server-side image decoding; Obital uses transformers.js only in the
  browser worker, and the shipped bundle contains no reference to libvips or
  to any sharp platform binding. Remove the entry once sharp 0.35.0 or later
  is reachable through that dependency.
- Lockfile hashes are verified against the PyPI JSON API, not assumed. A
  hand-edited version bump in June left `python-multipart` and `starlette`
  recorded with digests matching no published artifact, which made
  `--require-hashes` unsatisfiable without failing loudly anywhere the
  workflow was not running.
- Security workflows: `.github/workflows/engine-security-ci.yml` and
  `.github/workflows/package-security-ci.yml`
- Dependabot is currently disabled. See CHANGELOG for the rationale and the
  one-line restore command.
- E2EE plaintext migration: ``scripts/migrate_plaintext_rows.py`` re-encrypts
  rows stored before the write-site enforcement was deployed

## Further Reading

- `docs/architecture/ARCHITECTURE.md` — full system design including encryption chain and database schema
- `backend/README.md` — API endpoint authentication requirements
- `engine/docs/quiz/QUIZ_BACKEND_ARCHITECTURE.md` — grading and session security
