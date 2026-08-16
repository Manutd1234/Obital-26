# Obital coding standards

This guide keeps the Python backend, React frontend, scripts, and tests readable as the project grows.

## Organization

- `backend/app/api/` contains thin HTTP route handlers. Validation and orchestration belong in services.
- `backend/app/services/` contains business rules, persistence, encryption, and integrations.
- `backend/tests/` contains API, service, storage, security, and browser-runtime tests.
- `frontend/src/api/` contains typed HTTP clients; stateful UI behavior belongs in stores/hooks.
- `frontend/src/components/` contains presentational and feature components grouped by user journey.
- `engine/` remains a separately runnable pipeline and quiz package.
- `scripts/` contains operator, development, synchronization, quiz, and smoke-test utilities.
- `docs/` contains maintained architecture, planning, testing, product, and review material.

## Python

- Target Python 3.13, the version pinned in [`.python-version`](../../.python-version) and used by every CI workflow; use type hints for public functions and dataclasses/Pydantic models for structured data.
- Keep route handlers small: parse input, call a service, map the response, and return an HTTP-safe error.
- Prefer `pathlib.Path`, parameterized SQLAlchemy queries, and explicit UTF-8 file encoding.
- Raise domain-specific errors at service boundaries; do not swallow exceptions without logging context.
- Keep side effects (filesystem, database, network, model calls) behind injectable helpers so tests can isolate them.

## TypeScript and React

- Use strict TypeScript; define API response types next to the client that owns them.
- Keep components focused on rendering and user interaction. Put shared state in Zustand stores and reusable effects in hooks.
- Handle loading, empty, success, and error states explicitly for every network-backed view.
- Prefer accessible native controls, visible focus states, stable layouts, and non-jumping hover styles.
- Escape or safely render untrusted content. Markdown and HTML parsing must use the project sanitization boundary.

## Comments and naming

- Comments explain intent, invariants, security boundaries, or a surprising trade-off; they should not restate the code.
- Use descriptive `snake_case` Python names and `camelCase` TypeScript names. Constants are `UPPER_SNAKE_CASE`.
- Add a short module docstring when a module owns a non-obvious workflow or compatibility boundary.
- Update the relevant test and documentation when behavior, paths, or public commands change.

## Verification before review

Run the smallest relevant checks locally, then the maintained full groups when practical:

```powershell
python -m pytest backend/tests -q
python -m pytest engine/tests -q
cd frontend; npm run build
```

New behavior should include a positive case, a boundary or invalid-input case, and a regression test when it fixes a reported issue. Keep tests deterministic by mocking network/model providers and using temporary storage.
