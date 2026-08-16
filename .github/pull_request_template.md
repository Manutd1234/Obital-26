## Summary

### What Changed?

- 

### Why Is It Needed?

- 

## Scope

- [ ] Backend API (FastAPI)
- [ ] PostgreSQL schema or migration
- [ ] E2EE encryption / passphrase unlock
- [ ] AI/RAG orchestration pipeline
- [ ] Quiz grading engine
- [ ] Client-side browser JS (Web TUI)
- [ ] Tests or CI only
- [ ] Documentation only

## Backend Checklist

- [ ] Routes/controllers updated where needed
- [ ] Service/model layer updated where needed
- [ ] Alembic migration added where needed (avoid transaction pooler for DDL)
- [ ] SQL uses parameterized inputs for user-provided values
- [ ] E2EE encryption behavior considered for new fields
- [ ] Environment variables documented in `.env.example` where needed
- [ ] Tests added or updated
- [ ] README or developer docs updated
- [ ] Lint/type/test checks pass for the touched modules
- [ ] `CHANGELOG.md` updated for user-visible or behavior-changing work (or marked N/A)

## Database And Migration Notes

- New scripts:
- Apply order:
- Rollback notes:

## API Changes

| Method | Endpoint | Auth Required | Notes |
| --- | --- | --- | --- |
|  |  |  |  |

## Verification

Paste commands run and key results:

```bash
# Maintained unit evidence group (44 cases)
python -m pytest engine/tests/test_quiz_modes.py engine/tests/test_checkpoint_normalization.py backend/tests/test_auth_api.py backend/tests/test_stateless_storage.py -q

# Maintained API/integration evidence group (40 cases)
python -m pytest backend/tests/test_sync_api.py backend/tests/test_workspace_api.py backend/tests/test_stream_and_routes.py backend/tests/test_lesson_materials.py -q

# Browser runtime (40 cases; requires `python -m playwright install chromium`)
python -m pytest backend/tests/test_browser_js.py -q

# Frontend
cd frontend && npm ci && npm run build
```

These are the groups Core Quality CI runs. The full `pytest backend/tests/`
sweep is *not* used here: parts of it are a documented legacy baseline that
does not collect cleanly (see `backend/tests/README.md`), so it cannot pass
and pasting its output proves nothing.

Result summary:

- Verification command(s):

## Changelog

- [ ] Added a `CHANGELOG.md` entry for this PR
- [ ] N/A (internal refactor/no user-visible behavior change)

## Risk And Rollback

- Main risk:
- Rollback plan:

## Reviewer Notes

- 
