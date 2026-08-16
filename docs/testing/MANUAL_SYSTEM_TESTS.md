# Manual System-Test Cases

These cases cover real deployed behavior that unit and mocked browser tests cannot fully prove. Execute them against the release candidate and append one row to the execution log for each environment tested.

## Environment record

Record:

- release commit and deployment URL;
- tester ID (anonymous is acceptable);
- date/time;
- browser and version;
- OS and device;
- viewport/input method;
- network condition;
- local/cloud mode and whether a real LLM key is configured.

## Smoke cases

| ID | Mission | Steps | Expected result |
|---|---|---|---|
| SYS-01 | Create and unlock an account | Register username/PIN, confirm PIN, create passkey | Copy explains two credentials; one workspace opens |
| SYS-02 | Return to an account | Log out, enter PIN, then passkey | Correct failures are distinguished; successful unlock restores state |
| SYS-03 | Upload and search a document | Upload PDF/text/photo, search a phrase in its contents | Upload remains usable; search shows result/progress/clear empty state |
| SYS-04 | Ground Chat in a source | Mark passages or attach a Doc with `@`, then ask a question | Answer uses selected source context |
| SYS-05 | Stress ordinary Chat | Send ten ordinary questions including long Markdown/code prompts | No embedding download before send, freeze, forced reload, or raw Markdown |
| SYS-06 | Recover from chat failure | Interrupt network or use a failing provider configuration | Stream ends; retryable message appears; browser remains interactive |
| SYS-07 | Generate lesson and quiz | Request lesson, open artifact, complete checkpoint quiz | Progress is saved and visible on Dashboard |
| SYS-08 | Find Profile | From primary navigation and Dashboard, open Profile | Account/profile is discoverable without entering generic Settings first |
| SYS-09 | Use Friends search | Type, clear, search, add/remove a friend | Clear control does not move; state updates visibly |
| SYS-10 | Verify focus accounting | Run timer, pause after a measurable interval, resume/finish | Paused and final segments contribute to Today’s Focus |
| SYS-11 | Verify rewards loop | Complete study action and inspect rank/Shop | XP/currency change is attributable to completed study activity |
| SYS-12 | Cross-device sync | Push on device A; unlock and pull on device B | Docs, chats, lessons, rewards, and workspace continue correctly |
| SYS-13 | Responsive/keyboard smoke | Repeat core flow at phone viewport and keyboard-only | No blocked control, overflow, or inaccessible primary action |
| SYS-14 | Save Chat and Quiz to a Lesson | Save one chat and one generated quiz, choose a destination, open that Lesson, then save each again | Both appear under Saved from Chat; the quiz remains interactive; duplicates are reported instead of copied; checkpoint progress is unchanged |

## Compatibility matrix

At minimum, execute the core flow (SYS-01, 03, 05, 07, 10, 14) on:

| Browser | Windows | macOS | Mobile viewport |
|---|---:|---:|---:|
| Chrome | Required | Required | Android-sized |
| Edge | Required | Optional | — |
| Firefox | Required | Required | Responsive viewport |
| Safari | — | Required | iPhone-sized |

## Execution log

Do not mark unexecuted cases as passed.

| Date | Commit | Tester | Environment | Case IDs | Result | Evidence/issue |
|---|---|---|---|---|---|---|
| 2026-07-12 | pre-cleanup external build | UT-01 | Office Windows laptop, browser version not recorded | Broad exploratory session | Issues found | See [`MS3_USER_TESTING.md`](MS3_USER_TESTING.md) |
| _pending_ | `Ian-Cleanup` | _assign_ | Chrome/Windows | SYS-01–12 | Not run | Retest required after deployment |
| _pending_ | `Ian-Cleanup` | _assign_ | Safari/macOS | SYS-01,03,05,07,10,13 | Not run | Compatibility pass required |

Screenshots, videos, console logs, and issue links may be added under `docs/testing/evidence/` with the same case ID.
