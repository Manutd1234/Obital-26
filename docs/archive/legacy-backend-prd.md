# StudyBuddy Backend PRD

## 1. Executive Summary

StudyBuddy is a gamified educational platform where students complete learning modules across subjects such as Science, Math, and Coding. The backend must act as the secure system of record for curriculum content, progression state, quiz attempts, stars, points, streaks, leaderboard scores, shop purchases, and AI-assisted learning workflows.

The backend's core purpose is to provide reliable APIs that let the frontend render a progression map, unlock modules in order, submit quiz answers, award stars and points, synchronize user progress across devices, and maintain fair leaderboard competition. It should also support future AI/RAG learning features, offline-friendly content prefetching, and analytics needed to measure product success.

## 2. Goals And Non-Goals

### Goals

- Provide a secure, versioned REST API for the StudyBuddy frontend.
- Store curriculum content and user progress in a durable relational database.
- Support progression map states: `locked`, `unlocked`, `completed`.
- Support module types: `Warm Up`, `Apply`, and `Boss Run`.
- Award stars, XP/points, coins, and streak progress consistently.
- Maintain weekly and all-time leaderboard scores.
- Support Firebase Auth identity verification and cross-device sync.
- Provide observable success metrics for retention, completion, and engagement.
- Keep architecture modular enough to add AI/RAG, document scanning, and adaptive learning.

### Non-Goals For V1

- Real-money payments.
- Complex classroom/teacher administration.
- Full CMS authoring UI for curriculum editors.
- Multi-region active-active deployment.
- Fully personalized adaptive curriculum sequencing.

## 3. Tech Stack Constraint

Recommended and assumed stack for V1:

- Runtime: Node.js 20.
- API framework: Express.js.
- Primary database: Azure SQL using the `mssql` driver.
- Authentication: Firebase Auth, verified server-side with Firebase Admin SDK.
- Realtime sync: Firebase Realtime Database or Firestore as a mirror, with Azure SQL as source of truth.
- AI/RAG: Gemini API plus Azure AI Search for vector retrieval.
- Jobs: `node-cron` for scheduled backend jobs such as streak and leaderboard resets.
- Testing: Node test runner for pure unit tests, Jest + Supertest for API integration tests.
- Deployment target: Azure App Service or another Node-compatible container/app platform.

This stack is intentionally a modular monolith: one deployable backend application with clear folders for routes, controllers, services, models, jobs, and SQL scripts.

## 4. System Architecture Overview

### Recommended Pattern

Use a modular RESTful monolith for V1.

Rationale:

- The product is early-stage and benefits from fast iteration.
- A single deployable API avoids premature operational complexity.
- Clear service/module boundaries still allow future extraction into microservices.
- Most features share the same domain entities: users, progress, points, streaks, and content.
- Azure SQL transactions are important for atomic gamification flows such as `submit answer -> award points -> update leaderboard`.

### High-Level Components

- API Layer: Express routes and controllers that expose REST endpoints.
- Auth Layer: Firebase bearer-token verification middleware.
- Service Layer: Business logic for progression, scoring, streaks, shop purchases, AI orchestration, and sync.
- Model/Data Layer: Parameterized Azure SQL queries through `mssql`.
- Scheduled Jobs: Nightly streak processing and weekly leaderboard reset.
- Realtime Mirror: Firebase writes for low-latency frontend subscriptions.
- Search/AI Layer: Azure AI Search and Gemini for contextual chatbot support.
- Test Layer: Unit and integration tests with mocked Firebase and SQL.

### Request Flow Example

1. Frontend sends `POST /api/modules/:moduleId/submit` with Firebase bearer token.
2. Auth middleware verifies token and attaches `request.user`.
3. Controller validates payload.
4. Service grades answers and calculates stars, points, and coin rewards.
5. Model layer writes progress and ledger entries inside a SQL transaction.
6. Service optionally mirrors updated progress to Firebase.
7. API returns updated module state, user rewards, and next unlock state.

## 5. Core Entities And Database Schema

### Users

Stores identity profile metadata linked to Firebase Auth.

Fields:

- `id`: Firebase UID or backend UUID.
- `email`: unique email.
- `username`: display name.
- `created_at`: UTC timestamp.
- `last_seen_at`: optional activity timestamp.

Relationships:

- One user has many progress records.
- One user has many quiz attempts.
- One user has many currency ledger entries.
- One user has one streak record.
- One user has leaderboard score rows.

### Subjects

Top-level curriculum area.

Examples:

- Science.
- Math.
- Coding.

Fields:

- `id`: UUID.
- `name`: subject name.
- `icon_url`: optional UI icon.
- `order_index`: display order.

Relationships:

- One subject has many modules or topics.

### Modules

Represents a learning node on the progression map. In the current schema this can map to `levels`, while the product language can expose it as `modules`.

Module types:

- `Warm Up`: introductory low-stakes practice.
- `Apply`: standard application questions.
- `Boss Run`: capstone challenge for a topic or stage.

Fields:

- `id`: UUID.
- `subject_id`: FK to subject.
- `topic_id`: optional FK to topic.
- `title`: module title.
- `module_type`: `warm_up`, `apply`, `boss_run`.
- `order_index`: progression order.
- `unlock_rule`: JSON or structured rule.
- `points_available`: max points.
- `stars_available`: usually 3.

Relationships:

- One module has many questions.
- One module has many user progress records.

### Questions

Stores quiz or coding prompt data.

Fields:

- `id`: UUID.
- `module_id`: FK to module.
- `body`: question text.
- `question_type`: `mcq`, `free_text`, `code`, `flashcard`.
- `options`: JSON array for MCQs.
- `answer`: canonical answer or rubric reference.
- `explanation`: explanation shown after submission.
- `difficulty`: optional numeric tier.

Relationships:

- Many questions belong to one module.
- Questions may have many submitted answers/attempts.

### User Progress

Tracks completion and unlock status per module.

Fields:

- `id`: UUID.
- `user_id`: FK to user.
- `module_id`: FK to module.
- `status`: `locked`, `unlocked`, `completed`.
- `best_score`: best percentage or points.
- `stars`: 0-3 stars.
- `attempts`: number of attempts.
- `completed_at`: nullable UTC timestamp.
- `updated_at`: UTC timestamp.

Constraints:

- Unique `(user_id, module_id)`.

### Quiz Attempts

Stores each submission for auditing, analytics, and review.

Fields:

- `id`: UUID.
- `user_id`: FK to user.
- `module_id`: FK to module.
- `score`: score for this attempt.
- `stars_awarded`: stars earned on this attempt.
- `answers_json`: submitted answers.
- `created_at`: UTC timestamp.

### Currency Ledger

Append-only record of coin changes.

Fields:

- `id`: UUID.
- `user_id`: FK to user.
- `delta`: positive or negative integer.
- `reason`: `module_completed`, `weekly_bonus`, `shop_purchase`, `admin_adjustment`.
- `reference_type`: optional source category.
- `reference_id`: optional source ID.
- `created_at`: UTC timestamp.

Balance is calculated as `SUM(delta)` per user.

### Streaks

Tracks daily study streak state.

Fields:

- `user_id`: FK to user, primary key.
- `current_streak`: integer.
- `longest_streak`: integer.
- `last_activity_date`: date.
- `last_streak_processed_date`: date.
- `updated_at`: UTC timestamp.

### Leaderboard Scores

Stores weekly and all-time ranking aggregates.

Fields:

- `user_id`: FK to user.
- `weekly_points`: integer.
- `all_time_points`: integer.
- `rank`: materialized rank.
- `week_start_date`: date for weekly leaderboard versioning.
- `refreshed_at`: UTC timestamp.

Implementation options:

- V1: compute leaderboard from `progress_log` and materialize periodically.
- V2: maintain a dedicated aggregate table updated through events.

## 6. Key API Endpoints

### Authentication And User Session

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/me` | Return authenticated user profile and current account state. |
| POST | `/api/auth/sync-user` | Create or update backend user row after Firebase signup/login. |
| POST | `/api/analytics/events` | Record app/session events for DAU/MAU and PWA metrics. |

Authentication is handled by Firebase on the client. Backend endpoints requiring identity must verify `Authorization: Bearer <firebase_id_token>`.

### Subjects And Progression Map

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/subjects` | List subjects such as Science, Math, and Coding. |
| GET | `/api/subjects/:subjectId/map` | Return progression map with modules and user lock/completion state. |
| GET | `/api/topics/:topicId/levels` | Return topic modules/levels with lock status. |
| GET | `/api/modules/:moduleId` | Return full module payload including questions and notes. |
| GET | `/api/levels/:levelId/prefetch` | Return upcoming payloads for offline caching. |

### Quiz And Module Completion

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/modules/:moduleId/submit` | Submit answers, grade attempt, award stars/points, and unlock next module. |
| POST | `/api/progress` | Record completion from score for current implementation. |
| GET | `/api/progress/summary` | Return aggregate user progress by subject/module type. |
| POST | `/api/progress/mirror` | Mirror progress to Firebase for realtime sync. |

### Leaderboard

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/leaderboard?scope=weekly` | Return top 50 weekly leaderboard plus requesting user's rank. |
| GET | `/api/leaderboard?scope=all_time` | Return all-time leaderboard. |
| POST | `/api/admin/leaderboard/reset-weekly` | Admin-only manual weekly reset or recompute. |

### Gamification Shop

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/shop/items` | Return purchasable cosmetic or utility items. |
| POST | `/api/shop/buy` | Atomically check balance, deduct coins, and insert inventory. |
| GET | `/api/me/inventory` | Return owned active items. |

### AI And Learning Support

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/chat` | Context-aware Gemini/RAG tutoring response. |
| POST | `/api/chat/feedback` | Store satisfaction rating for AI response quality. |
| POST | `/api/documents/upload` | OCR scanned notes, chunk, embed, and index for retrieval. |

### Metrics

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/metrics/success` | Return DAU/MAU, streak retention, chatbot satisfaction, completion rate, and PWA install rate. |
| GET | `/api/offline/manifest` | Return backend-supported PWA, TTS, and sync capabilities. |

## 7. Gamification Logic

### Stars

Stars represent mastery for a completed module.

Suggested scoring:

- 3 stars: score >= 90%.
- 2 stars: score >= 70%.
- 1 star: score >= 50%.
- 0 stars: score < 50%, not considered completed unless product chooses otherwise.

Only newly earned stars should grant incremental rewards. If a user previously earned 2 stars and later earns 3, only 1 new star is rewarded.

### Points And XP

Points drive leaderboards and progression status.

Suggested formula:

- Base points by module type:
- `Warm Up`: 10 points.
- `Apply`: 20 points.
- `Boss Run`: 50 points.
- Star multiplier:
- 1 star: 1.0x.
- 2 stars: 1.25x.
- 3 stars: 1.5x.

The backend should compute points server-side only. The frontend may preview possible rewards, but cannot be trusted to submit reward values.

### Coins

Coins are spendable currency for shop items.

Rules:

- Award coins when new stars are earned.
- Store all coin movement in `currency_ledger`.
- Never store balance as the only source of truth.
- Shop purchases must be SQL transactions:
- Lock user balance inputs.
- Check sufficient balance.
- Insert negative ledger entry.
- Insert inventory row.
- Commit all or nothing.

### Streaks

Streaks reward daily study behavior.

Rules:

- Completing any module marks user activity for the day.
- Nightly job checks whether user was active yesterday.
- If active, increment streak.
- If streak hits a multiple of 7, award weekly bonus coins.
- If inactive and user owns `freeze-basic`, consume one freeze and preserve streak.
- If inactive and no freeze exists, reset current streak to 0.

### Progression And Unlocking

Default unlock model:

- First module in a subject/topic starts unlocked.
- Completing a module unlocks the next module.
- `Boss Run` unlocks only after required `Warm Up` and `Apply` modules are completed.
- Backend should calculate lock status, not the frontend.

### Weekly Leaderboard Reset

Weekly leaderboard should reset or roll over on a fixed schedule, e.g. Monday 00:05 Asia/Singapore.

Process:

1. Snapshot final weekly ranks.
2. Write rewards for top users if applicable.
3. Reset `weekly_points` to 0 or start a new `week_start_date` partition.
4. Preserve `all_time_points`.
5. Emit analytics/admin logs.

## 8. Security And Authentication

### Authentication

- Firebase Auth is the identity provider.
- Every private endpoint requires a Firebase ID token.
- Backend verifies token using Firebase Admin SDK.
- Backend maps `uid` to internal user profile.

### Authorization

- Users may only access their own progress, inventory, documents, and metrics.
- Admin endpoints require admin custom claims or a backend role table.
- Leaderboard public rows should expose only display-safe profile fields.

### Data Protection

- Use HTTPS only in production.
- Store secrets in environment variables or managed secret store.
- Do not log bearer tokens, private keys, raw passwords, or sensitive AI prompts.
- Use parameterized SQL statements for all user inputs.
- Validate file uploads by MIME type, size, and content handling rules.
- Enforce rate limits on auth-sensitive, AI, upload, and quiz submission routes.

### Session Management

- Firebase ID tokens are short-lived.
- Refresh token handling remains client/Firebase-managed unless a custom web session layer is introduced.
- If custom backend sessions are added later, store refresh tokens as hashed values and use `httpOnly`, `secure`, `sameSite` cookies.

## 9. Scalability And Performance

### API Performance

- Use Azure SQL connection pooling.
- Keep common read payloads compact and cache-friendly.
- Paginate large lists such as attempts, events, and leaderboard history.
- Use indexes on:
- `progress_log(user_id, level_id)`.
- `progress_log(completed_at)`.
- `currency_ledger(user_id, created_at)`.
- `leaderboard(weekly_points)`.
- `analytics_events(event_type, created_at)`.

### Peak Study Times

Expected spikes:

- Evening study hours.
- Weekend exam-prep sessions.
- Weekly leaderboard reset.
- Release of new content.

Mitigations:

- Keep quiz submission transactional but short.
- Avoid recomputing full leaderboard on every request.
- Use materialized leaderboard snapshots or cached top 50.
- Queue or batch heavy analytics processing.
- Rate-limit AI/chat and document upload routes.

### Leaderboard Reset

Leaderboard reset should be idempotent.

Requirements:

- Use a `week_start_date` or reset marker.
- Lock reset job to prevent duplicate execution.
- Store reset audit logs.
- Allow manual admin recompute for recovery.

### Offline And Prefetch

Backend should support frontend PWA behavior by:

- Returning deterministic cache-friendly module payloads.
- Providing `GET /api/levels/:levelId/prefetch`.
- Avoiding user-private leakage in cached shared resources.
- Including version fields for content invalidation in V2.

## 10. Observability And Success Metrics

Required backend metrics:

- DAU/MAU ratio.
- 7-day streak retention.
- AI chatbot satisfaction.
- Level completion rate.
- PWA install rate.
- Quiz submission latency.
- Error rate by endpoint.
- Leaderboard reset duration.
- AI response latency and failure rate.

Minimum logging:

- Request ID.
- User ID or anonymized UID where appropriate.
- Endpoint and status.
- Latency.
- Error class.

Avoid logging:

- Authorization headers.
- Raw uploaded document content unless explicitly needed and protected.
- Sensitive student personal data.

## 11. Admin And Operations Requirements

Admin capabilities for V1/V1.5:

- Trigger leaderboard recompute.
- Inspect success metrics.
- Seed sample content.
- View chatbot feedback.
- Audit user currency ledger for support/debugging.
- Disable problematic shop item or content module.

Operational requirements:

- Health endpoint.
- CI checks for syntax and tests.
- SQL migration order documented.
- Environment variables documented in `.env.example`.
- Rollback notes for every schema-changing PR.

## 12. Future Phases (V2)

### Architecture Evolution

Potential extra services:

- Content service for curriculum authoring and publishing.
- Gamification service for points, streaks, inventory, and leaderboard.
- AI service for chat, RAG, document search, and safety filters.
- Analytics service for events and product metrics.

Extraction should happen only after traffic or team size justifies the operational cost.

### Data And Content

- Content versioning and release channels.
- Rich module graph with prerequisites beyond linear order.
- Adaptive module recommendations.
- Teacher-created classrooms.
- School or cohort-level leaderboards.

### AI And Personalization

- Mistake-history-aware tutor.
- Fine-tuned Singapore curriculum model.
- Per-user learning profile.
- Retrieval over uploaded notes and official curriculum notes.
- Safety and hallucination monitoring.

### Scale And Reliability

- Redis cache for leaderboard and hot content.
- Background worker queue for uploads, embeddings, analytics, and reset jobs.
- Read replicas or reporting database for analytics.
- Blue/green deployments.
- Structured distributed tracing.

## 13. Acceptance Criteria

The V1 backend is considered ready when:

- Authenticated users can fetch subjects and progression map state.
- Users can submit module answers and receive server-calculated stars/points.
- Progress persists and unlocks the next module.
- Weekly leaderboard returns top 50 plus requesting user's rank.
- Streaks are processed nightly and freeze items work.
- Shop purchases are atomic and cannot overspend.
- Firebase mirror updates can support cross-device sync.
- Core APIs have integration tests.
- SQL schema and seed scripts can initialize a demo database.
- CI runs backend syntax checks and tests on PRs.

## 14. Open Questions

- Should StudyBuddy use Firebase UID directly as `users.id`, or map Firebase UID to a backend UUID?
- Should `Boss Run` require all prior modules or only a minimum star threshold?
- Should weekly leaderboard be global, per subject, or both?
- Should failed quiz attempts count toward activity streaks?
- Should coins be awarded on first completion only, or incremental star improvement only?
- What moderation/safety policy should apply to AI chatbot responses?
