# Obital Product Requirements

## Product summary

Obital is a privacy-first, gamified AI study companion. It turns a learner’s own source material into grounded conversations, structured AI lessons, quizzes, progress, and rewards while keeping private study data inside one encrypted account workspace.

## Problem

Study tools commonly split source storage, tutoring, notes, quizzes, focus tracking, and motivation into disconnected products. Learners must repeatedly move context between them, and AI features can become unreliable or opaque under real device constraints.

Obital’s value is not the number of tabs. Its value is a dependable connected loop from source material to learning and motivation.

## Goals

- Make uploaded material immediately useful in Chat and Lessons.
- Make the difference between source Docs and curated AI Lessons obvious.
- Provide robust tutor responses without freezing the user’s browser.
- Turn lessons and quizzes into visible progress, XP, rank, and cosmetic rewards.
- Protect private content with a separate encryption passkey and optional encrypted sync.
- Support a first-time user without developer explanation.

## Non-goals for the current final phase

- Shipping a separate native/mobile client.
- Adding TTS before the core flows meet compatibility and reliability targets.
- Adding game modes solely to increase feature count.
- Supporting multiple vaults per account.

## Primary users

| User | Need |
|---|---|
| Student with course material | Upload sources, ask grounded questions, and revise through quizzes |
| Independent learner | Turn a topic into a structured lesson and track progress |
| Motivation-driven learner | Earn visible rewards for genuine study activity |
| Privacy-conscious learner | Sync devices without exposing plaintext study history |
| First-time or non-technical user | Understand each screen and recover from errors without technical terminology |

## Core user journeys

### 1. First use

1. Create an account name and sign-in PIN.
2. Create the separate workspace encryption passkey with an explanation of why it exists.
3. Enter the app with clear next actions: upload a source, ask Chat, or create a Lesson.

### 2. Source-to-study loop

1. Upload a document in Docs.
2. Search or read extracted content.
3. Mark passages or attach the document with `@` in Chat.
4. Ask a grounded question or request a lesson.
5. Open the resulting curated lesson in Lessons.
6. Complete the embedded quiz.
7. See XP/progress change on Dashboard and use earned rewards in Shop.

### 3. Focus loop

1. Start the Workspace timer.
2. Pause or finish the session.
3. Retain the elapsed segment in Today’s Focus and Dashboard.

### 4. Cross-device loop

1. Push encrypted account data from one device.
2. Sign in on another device.
3. Unlock with the same workspace passkey.
4. Pull and continue with the same Docs, Lessons, chats, and progress.

## Functional requirements

| Area | Requirement | Acceptance signal |
|---|---|---|
| Authentication | Explain PIN vs. workspace passkey and one-workspace model | New tester can describe both credentials correctly |
| Docs | Parse supported files and search names/contents | Search produces results, progress, empty, or error state |
| Chat | Stream useful Markdown answers without initializing browser ML for ordinary questions | Ten consecutive turns without freeze |
| Grounding | Let users mark passages or attach a document with `@` | Chat identifies and uses selected context |
| Lessons | Generate curated notes with sources and embedded quizzes | Finished artifact opens from Lessons/Dashboard |
| Quiz | Grade and explain answers and update progress | Result persists after refresh |
| Profile | Provide a prominent entry point | Accessible directly from primary navigation/Dashboard |
| Social | Search/add/remove friends without layout shifts | Clear control remains stable on hover/focus |
| Timer | Count paused and completed focus segments | Today’s Focus increases after a meaningful pause |
| Rewards | Connect quizzes/focus to progression and Shop | Reward changes are visible and attributable |
| Sync | Transfer encrypted account state between devices | Two-device round trip succeeds without plaintext cloud data |

## Non-functional requirements

### Reliability

- External AI, indexing, and network failures must not freeze the browser.
- Long-running work must expose progress and bounded failure states.
- User-authored content must not be discarded when enrichment fails.

### Performance

- Ordinary chat submission must not download or execute the embedding model.
- Streaming UI updates must be batched to avoid excessive Markdown rerenders.
- Dashboard sections should render progressively.

### Compatibility

- Support current Chrome, Edge, Firefox, and Safari.
- Support Windows and macOS as primary desktop targets.
- Maintain usable responsive layouts at representative phone widths.
- Respect reduced-motion preferences and keyboard interaction.

### Security and privacy

- PINs are salted and hashed.
- Workspace data is decrypted only with the passkey-derived key.
- Unlock sessions are account/client-bound and expire.
- Cloud artifacts remain encrypted at rest.

### Accessibility

- Controls have accessible names and visible states.
- Primary journeys are keyboard operable.
- Text contrast and layout remain usable in light and dark themes.
- TTS remains a future enhancement, not a substitute for accessible structure.

## Success metrics

- Core-journey completion rate among uncoached testers.
- Chat success/failure and time-to-first-token rates.
- Browser freeze or forced-reload rate.
- Lesson-generation completion rate.
- Quiz completion and return-to-study rate.
- Cross-device sync success rate.
- Number of P0/P1 defects found before versus after release.

## Dependencies and risks

| Risk | Mitigation |
|---|---|
| LLM timeout or malformed output | Bounded calls, validation, retry/fallback, visible errors |
| Browser memory pressure | Keep embedding work off ordinary chat path; use workers only for explicit indexing |
| Confusing security model | Explain credentials at the moment they are requested |
| Feature fragmentation | Design and test connected journeys instead of isolated screens |
| Cross-platform drift | Maintain the compatibility matrix in [PLAN.md](PLAN.md) |

See [workflow.md](workflow.md) for journey details and [DATA_PROCESSING_FLOW.md](../architecture/DATA_PROCESSING_FLOW.md) for the underlying information flow.
