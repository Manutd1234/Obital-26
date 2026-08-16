# MS3 User Testing

## Purpose

MS3 prioritizes a robust connected experience over additional feature count. This record documents real user feedback, the product decisions made from it, and what still requires retesting. It intentionally separates **observed**, **fixed**, and **not yet verified** states.

## Session UT-01 — external exploratory test

| Field | Record |
|---|---|
| Date recorded | 12 July 2026 |
| Participant | UT-01, external reviewer (anonymous) |
| Device | Office Windows laptop described as high-specification |
| Browser/version | Not recorded |
| Build | Pre-`Ian-Cleanup` review build |
| Method | Unmoderated exploratory/system test using sample accounts and files; cross-device sync used two devices |
| Goal | Attempt the advertised features and report confusion, failures, and UI defects |

## Tasks and findings

| Area | Observed evidence | Review outcome | Product response | Retest |
|---|---|---|---|---|
| Authentication | PIN login worked, but the separate vault key and one-vault concept were unexplained | Marginal | Onboarding now labels sign-in PIN vs workspace passkey and explains the single encrypted workspace | Pending |
| Curated AI notes | Reviewer could not tell whether notes were in Docs or Lessons; Docs search appeared unresponsive | Not accepted | README/UI distinguish source Docs from curated Lessons; Docs search is debounced text/content search with progress | Pending |
| Sync/Contribute | Sample files transferred between two devices | Accepted | Preserve behavior and retain integration coverage | Passed in reviewed build |
| Chat | Browser froze 6/10 sends; 3/10 returned `OSError`; one success was too short and showed raw Markdown | Not accepted | Ordinary chat no longer initializes browser embeddings; streaming Markdown renders; updates are batched; resource errors are retryable; tutor prompt requests useful depth | Pending ten-turn SYS-05 retest |
| PDF/photo parser | Parsing worked; “Rebuild index” was backend-facing terminology | Accepted with wording issue | Renamed to **Refresh smart search** and documented when it is needed | Pending wording check |
| Quiz/flashcards | Could not be fully exercised because Chat failed | Marginal | Unblocked by Chat reliability work; lesson/quiz automated cases remain in quality suite | Pending end-to-end retest |
| Profile/Dashboard | Functionality worked, but Profile was hidden inside Settings | Marginal | Profile added to primary navigation and Dashboard | Pending discoverability retest |
| Friends | Add-friend flow worked; clear X shifted on hover | Mostly accepted | Clear control uses stable positioning and accessible name | Pending hover/focus retest |
| Leaderboard | Worked | Accepted | Preserve with smoke/regression checks | Passed in reviewed build |
| Workspace timer | Paused time did not increase Today’s Focus | Marginal | Pausing now persists the elapsed segment | Pending timer retest |
| Shop | Could not earn enough XP because Chat/quiz path failed | Marginal | Retest after source → Chat → Lesson → Quiz → reward loop is stable | Pending |

## Resulting MS3 priorities

1. Reliability and recovery of Chat and lesson/quiz flows.
2. Connected navigation between Docs, Chat, Lessons, Dashboard, rewards, and Shop.
3. Clear onboarding and user-facing terminology.
4. Cross-browser/device compatibility and adversarial “try to break it” testing.
5. Regression coverage for each reusable invariant found through testing.

Native app development, TTS, and additional game modes remain deferred until the core release gates pass.

## Retest protocol

UT-01 findings are not considered closed merely because code changed. A tester should execute the corresponding cases in [`MANUAL_SYSTEM_TESTS.md`](MANUAL_SYSTEM_TESTS.md), ideally without coaching, and record:

- whether the task completed;
- time/attempts;
- visible hesitation or misunderstood terminology;
- browser responsiveness and console errors;
- screenshots/video where useful;
- issue link for every failure.

Suggested success criteria:

- 10/10 ordinary chat sends complete without freeze or forced reload;
- first-time tester correctly explains PIN vs passkey and Docs vs Lessons;
- complete source → lesson → quiz → XP/Shop journey succeeds;
- focus total increases after pause;
- core journey passes Chrome, Edge, Firefox, and Safari targets.

## Future user-test log

| Session | Date | Build/commit | Tester/environment | Result | Linked evidence |
|---|---|---|---|---|---|
| UT-01 | 2026-07-12 | Pre-cleanup build | External reviewer, office Windows laptop | Issues found | This document |
| UT-02 | Pending | `Ian-Cleanup` deployment | New/uncoached tester | Not run | Add issue/video links |
| UT-03 | Pending | Release candidate | Cross-browser/device group | Not run | Add compatibility results |
