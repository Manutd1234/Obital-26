# Repository scripts

Scripts are grouped by the environment or responsibility they operate on:

- `dev/` — local backend, frontend, unified startup, and production bundle build.
- `sync/` — account-scoped cloud pull/push recovery commands and reset utilities.
- `quiz/` — standalone engine quiz CLI startup.
- `smoke/` — manual smoke checks that require a running backend or external provider.
- The `scripts/*.py` utilities are repository audits and migrations; they remain at this level because CI and documentation invoke them directly:

| Script | Used by |
|---|---|
| `check_licenses.py` | License CI — every direct dependency must appear in `THIRD_PARTY_NOTICES.md` and resolve to a licence allowed by `license-policy.json` |
| `check_author_attribution.py` | Author Attribution CI — `LICENSE`, `CITATION.cff`, `AUTHORS.md` and `frontend/package.json` must agree on the author |
| `check_npm_audit.py` | Package Security CI — fails on any high or critical npm advisory except those allowlisted in the script with a reason and a removal condition |
| `inspect_db_indexes.py` | Manual database index inspection |
| `migrate_plaintext_rows.py` | One-off migration of pre-E2EE plaintext rows |

All PowerShell entry points resolve the repository root from `$PSScriptRoot`, so they can be launched from any working directory.
