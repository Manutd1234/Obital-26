# Frontend

Obital has two frontends, both connected to the FastAPI backend:

| Frontend | Stack | Purpose |
|---|---|---|
| **React Web UI** | React 18 + TypeScript + Vite + Tailwind | Browser-based SPA |
| **Textual TUI** | Python Textual + httpx | Desktop terminal app |

---

## React Web UI

### Setup

```powershell
cd frontend
npm install
```

### Development (with hot-reload)

Start the backend and Vite dev server in separate terminals:

```powershell
# Terminal 1: backend
..\scripts\dev\run_backend.ps1

# Terminal 2: frontend dev server
cd frontend
npm run dev
```

Open **http://localhost:5173**. Vite proxies all API routes to `http://localhost:8000` via `vite.config.ts`.

### Production

The backend serves the React build at `GET /` from `backend/app/templates/index.html`. All API calls are same-origin — no proxy needed.

### API Layer

All API calls go through `frontend/src/api/client.ts`, which uses relative URLs and attaches auth headers (`X-Unlock-Token`, `X-Client-Id`).

| API Module | Endpoints | Notes |
|---|---|---|
| `api/auth.ts` | `/auth/register`, `/auth/login`, `/auth/unlock`, `/auth/lock`, `/auth/bootstrap` | Bootstrap = unlock + sync + data fetch in one call |
| `api/chat.ts` | `/chat/sessions` (POST), `/chat/sessions/{id}` (GET), `/chat/sessions/{id}/messages/stream` (POST SSE) | SSE streaming for token-by-token replies |
| `api/docs.ts` | `/docs`, `/docs/{id}`, `/docs/search`, `/docs/upload` | Bare arrays returned (not wrapped) |
| `api/lessons.ts` | `/lessons`, `/lessons/{id}`, `/lessons/{id}/attempt`, `/lessons/{id}/restart` | Maps backend `id` → `lesson_id` |
| `api/quiz.ts` | `/quiz/build`, `/quiz/grade` | Quiz generation and server-side grading |
| `api/rewards.ts` | `/rewards/dashboard` | Summary stats + score trend |
| `api/settings.ts` | `/settings` (GET/PATCH) | System prompt configuration |
| `api/sync.ts` | `/sync/push`, `/sync/pull` | Cloud sync (requires unlock token) |
| `api/pipeline.ts` | `/pipeline/generate-curriculum`, `/pipeline/generate-topic-qa`, `/pipeline/save` | Curriculum and QA generation |
| `api/usage.ts` | `/usage`, `/usage/tokens` | Token/doc/lesson usage tracking |

### SSE Streaming

The chat stream endpoint (`POST /chat/sessions/{id}/messages/stream`) sends Server-Sent Events as JSON in `data:` lines:

```
data: {"event":"token","data":"hello","session_id":"abc"}
data: {"event":"done","data":"","session_id":"abc"}
```

Parsed in `frontend/src/lib/sse.ts` and consumed by `useChatStream` hook.

### State Management

| Store | File | Purpose |
|---|---|---|
| `authStore` | `stores/authStore.ts` | Username, PIN, passphrase, unlock token, client UUID |
| `chatStore` | `stores/chatStore.ts` | Sessions, messages, streaming state |
| `docStore` | `stores/docStore.ts` | Documents, active doc, selected chunk IDs |
| `lessonStore` | `stores/lessonStore.ts` | Lessons, active lesson, checkpoint index |
| `uiStore` | `stores/uiStore.ts` | Modal open/close state, toasts |

### Components

```
components/
  auth/           LoginOverlay — multi-step auth (username → PIN → passphrase)
  chat/           ChatView, MessageBubble, QuizWidget, ToolCallCard
  dashboard/      DashboardPane
  fullScreen/     DashboardFullScreen, DocFullScreen, LessonFullScreen
  layout/         AppShell, ChatHistorySidebar, Footer
  shared/         SettingsModal
```

### Build

```powershell
cd frontend
npm run build
```

Output goes to `frontend/dist/`. For production, copy to `backend/app/templates/` or deploy to Vercel.

---

## Textual TUI

### Setup

```powershell
pip install -r frontend/requirements.txt
```

### Run

Preferred (unified):

```powershell
..\scripts\dev\run_obital.ps1
```

Manual split startup:

1. Start backend: `..\scripts\dev\run_backend.ps1`
2. Start frontend: `..\scripts\dev\run_frontend_textual.ps1`

Default backend URL is `http://127.0.0.1:8000` (override via `OBITAL_BACKEND_URL` env var).

### Text Controls

- The center pane runs the login flow via the `engine.quiz.quiz_chatbot_cli` subprocess bridge.
- After a successful sign-in, all subsequent chat messages are seamlessly routed directly through the **FastAPI backend** REST client.
- `/retry` resets the active FastAPI chat session and initializes a fresh session on the next prompt.
- Use the same prompts/commands as the engine CLI during the login/auth phase.
- Startup status lines are shown in-chat:
  - `[ui] starting CLI bridge...`
  - `[ui] CLI bridge ready`
  - `[ui] CLI process exited (code X)` on unexpected exit

### Keyboard Shortcuts

| Key | Action |
|---|---|
| `Ctrl+S` | Open Settings Window |
| `Ctrl+D` | Open Rewards Dashboard |
| `F6` | Check backend health |
| `F7` | Sync Cloud (push) |
| `F8` | Sync Cloud (pull) |
| `F9` | Open native Windows File Dialog to upload |

### Chat Commands

| Command | Description |
|---|---|
| `/lesson <n>` | Select lesson row in left pane |
| `/lesson start <topic>` | Start a new lesson on a topic |
| `/lesson status` | Show active lesson checkpoint progress |
| `/lesson quiz` | Run the next checkpoint quiz |
| `/lesson answer A,B,C,...` | Submit quiz answers for grading |
| `/lesson continue` | Continue an in-progress lesson |
| `/lesson restart <n>` | Restart a lesson from checkpoint 1 |
| `/lessons` | List all lessons with status and progress |
| `/doc <n>` | Select doc row in right pane |
| `/chunk <n>` | Select chunk row in right pane |
| `/search <query>` | Refresh right pane docs |
| `/upload` | Open native Windows File Picker dialog |
| `/upload C:\path\file.txt` | Upload a specific file by path |
| `/settings` | Open the Settings window |
| `/dashboard` | Open the Rewards Dashboard window |

### Settings Window

- Open with the `Settings` button at the bottom-left, type `/settings` in chat, or press `Ctrl+S`.
- The window uses backend `GET/PATCH /settings`.
- `System Prompt` is editable and saved per-account to `engine/accounts/{account_id}/config/chat_system_prompt.md`.
- Use `Reload` to fetch current server settings and `Save` to persist changes.

### Rewards Dashboard

- Open with the `Dashboard` button at the bottom-left, type `/dashboard` in chat, or press `Ctrl+D`.
- The dashboard uses backend rewards APIs to display:
  - summary stats (points, quizzes, accuracy)
  - recent score trend graph (sparkline)
  - recent reward-session deltas

### Notes

- Left/right panes refresh from backend every 5 seconds after successful CLI login.
- Doc pane behavior:
  - top list = docs
  - middle list = chunk previews
  - bottom area = full selected chunk text (scrollable)
