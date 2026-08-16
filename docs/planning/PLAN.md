# Obital Final-Phase Plan

## Direction

Obital has reached the intended feature breadth for the current milestone. The final phase prioritizes cohesion, reliability, compatibility, and demonstrability over adding a separate mobile application or text-to-speech feature.

The product should feel like one study loop rather than a collection of tabs:

```text
Upload or choose a source -> ask Chat -> create a Lesson -> take a Quiz
-> earn XP and rank -> use rewards in the Shop -> return to study
```

## Priorities

### P0 — Reliability of the core loop

- Keep ordinary chat responsive on modest laptops.
- Provide bounded timeouts, useful progress states, retryable errors, and cancellation.
- Ensure lesson and quiz generation cannot strand the user in a loading state.
- Preserve work when external AI or indexing services fail.
- Treat authentication, encryption, and cross-device sync as release-critical paths.

### P0 — Connected and discoverable UI

- Explain the PIN/passkey distinction during onboarding.
- Make Profile and progress easy to find.
- Clearly distinguish uploaded Docs from AI-generated Lessons.
- Use user-facing language instead of backend terminology.
- Lead users from sources to Chat, Lessons, quizzes, rewards, and Shop.
- Remove controls that do not help a normal user complete a task.

### P1 — Compatibility and adversarial QA

Test the same release candidate across:

| Dimension | Minimum matrix |
|---|---|
| Browsers | Current Chrome, Edge, Firefox, Safari |
| Desktop OS | Windows 11, macOS, one Linux distribution |
| Mobile viewport | iPhone Safari-sized viewport and Android Chrome-sized viewport |
| Input | Mouse, keyboard-only, touch-sized controls |
| Network | Normal, slow, interrupted, offline after initial load |
| Account state | New account, returning account, wrong credentials, expired unlock session |
| Data | No docs, one small doc, large PDF, malformed/unsupported file, many docs |
| AI | Healthy provider, timeout, rate limit, malformed response, missing API key |

Recruit testers who did not build the app and ask them to complete missions without coaching. Record where they hesitate, what they misinterpret, and how they recover from failure.

### P1 — Gamification refinement

- Keep XP, rank, leaderboard, Shop, quizzes, and focus time driven by real study actions.
- Make reward changes explainable and immediately visible.
- Prevent rewards from depending on a single fragile AI path.
- Prefer a polished progression loop over additional game modes.

### Deferred until quality gates pass

- Native/mobile application development.
- Text-to-speech.
- Additional game modes or social mechanics.
- New feature-count targets without a validated user journey.

Deferral is not cancellation. These items can resume after the release candidate meets the compatibility and reliability gates below.

## Release gates

A final-phase release candidate is ready to demonstrate when:

- Ten consecutive ordinary chat turns complete without a browser freeze.
- Uploaded-document search returns visible results or a clear empty state.
- A user can create a lesson and complete its quiz without developer intervention.
- Markdown, loading, error, and retry states render correctly.
- Paused focus time is retained.
- The core journey passes on Chrome, Edge, Firefox, and Safari.
- The responsive layout passes desktop and representative mobile viewports.
- Authentication and sync pass on two separate devices.
- No known P0 issue remains open.
- A first-time tester can explain the difference between Docs and Lessons and between the PIN and workspace passkey.

## Current cleanup scope

The `Ian-Cleanup` work focuses on the issues identified during external testing:

- clarify authentication and the single encrypted workspace;
- make curated AI notes discoverable;
- eliminate browser-side embedding work from ordinary chat;
- render streaming Markdown;
- improve chat answers and resource-error messaging;
- rename backend-facing document-index language;
- surface Profile;
- stabilize the Friends clear control;
- count paused timer segments;
- rebuild and verify the production frontend;
- align project documentation with the real product flow.

## Definition of done for every fix

1. Reproduce or document the original failure.
2. Implement the smallest cohesive fix.
3. Add automated regression coverage when practical.
4. Test the adjacent user journey, not only the changed component.
5. Verify the production build, not only the development server.
6. Update user and technical documentation.
7. Record remaining limitations honestly.
