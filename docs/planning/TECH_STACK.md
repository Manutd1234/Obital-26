# Obital Technology Stack

## Stack overview

| Layer | Technologies | Responsibility |
|---|---|---|
| Web client | React 18, TypeScript, Vite, Tailwind CSS, Zustand | Responsive product UI and client state |
| Rich rendering | React Markdown, remark-math, rehype-katex, KaTeX | Tutor and lesson Markdown/math |
| Visualizations | Recharts | Dashboard progress charts |
| Browser workers | Web Workers, Transformers.js, ONNX Runtime Web | Explicit document indexing without blocking the main UI thread |
| API | FastAPI, Pydantic, Uvicorn | Validation, REST/SSE transport, routing |
| AI orchestration | OpenAI-compatible SDK, DeepSeek-compatible models, tool calling | Tutor, lesson planning/generation/review, structured generation |
| Retrieval | MiniLM embeddings, cosine search, keyword search | Ground answers and lessons in user material |
| Local data | SQLite and account-scoped files | Offline/local development and primary local state |
| Cloud data | PostgreSQL/Supabase, SQLAlchemy, Alembic | Optional cloud persistence and migrations |
| Encryption | Argon2id, AES-GCM/cryptography, PBKDF2-HMAC-SHA256 | Workspace encryption, key wrapping, PIN verification |
| Testing | pytest, FastAPI TestClient, Playwright, TypeScript compiler, Vite build | Service, API, browser-logic, and production-build checks |
| Deployment | FastAPI static serving, Vercel entry point, PowerShell scripts | Local and hosted operation |

## Frontend

The primary browser experience lives in `frontend/src`.

- **Zustand stores** isolate authentication, chat, documents, lessons, workspace, rewards, and UI state.
- **SSE streaming** delivers tutor tokens and structured progress events.
- **React Markdown** renders completed and in-progress answers.
- **Web Workers** isolate expensive embedding/indexing work from the main UI thread.
- **Vite** produces the static bundle copied to `backend/app/templates` for same-origin production serving.

Ordinary chat deliberately avoids the browser embedding worker. Document context is attached explicitly through marked passages or `@` documents. This is a product reliability decision as well as a performance optimization.

## Backend

The backend lives in `backend/app` and separates HTTP routing from services:

- `api/` validates and shapes HTTP requests and responses.
- `services/chat_service.py` persists and orchestrates chat turns.
- `services/chat_engine.py` builds model context and streams the agent response.
- `services/lesson_harness.py` runs bounded background lesson generation.
- `services/storage.py` abstracts filesystem, SQLite, and stateless database storage.
- `services/account_sync.py` encrypts and merges optional cloud sync state.
- `services/unlock_*` enforce passkey-derived unlock sessions and lockout controls.

## AI and retrieval

Obital uses an OpenAI-compatible client so the configured provider can be changed without rewriting the product layer. Generated structured data is validated before it becomes a lesson or quiz.

Retrieval has two paths:

1. Fast filename/content search for predictable Docs behavior.
2. Optional semantic vectors for smart retrieval and explicit indexing.

The product must remain usable when semantic indexing is unavailable.

## Storage modes

| Mode | Configuration | Intended use |
|---|---|---|
| Local | `LOCAL_DATABASE_URL=sqlite:///...` | Local development and offline-first use |
| Cloud/stateless | `DATABASE_URL=postgresql+psycopg://...` | Hosted API using PostgreSQL/Supabase |
| Hybrid sync | Both URLs configured | Local primary data with encrypted push/pull |

## Compatibility targets

| Surface | Target |
|---|---|
| Browsers | Current Chrome, Edge, Firefox, Safari |
| Desktop | Windows 11 and current macOS; Linux smoke test |
| Responsive | Representative iPhone and Android viewport widths |
| Input | Mouse, keyboard, and touch-sized targets |
| GPU | WebGPU when available; WASM fallback for explicit indexing |

Features may not assume WebGPU. Browser-specific capability checks and clear fallbacks are required.

## Engineering conventions

- Keep account scope explicit at service and storage boundaries.
- Validate all external model output.
- Bound background concurrency and request timeouts.
- Preserve uploaded content even if enrichment fails.
- Prefer user-facing terms in the UI and implementation terms in technical docs.
- Build the production frontend after UI changes.
- Add regression tests for failures found through external testing.

## Key references

- [ARCHITECTURE.md](../architecture/ARCHITECTURE.md) — component and security architecture
- [DATA_PROCESSING_FLOW.md](../architecture/DATA_PROCESSING_FLOW.md) — data lifecycle
- [SECURITY.md](../../SECURITY.md) — threat model
- [backend/README.md](../../backend/README.md) — API and runtime configuration
- [frontend/README.md](../../frontend/README.md) — frontend development
