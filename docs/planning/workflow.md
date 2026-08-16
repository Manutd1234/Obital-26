# Obital Workflows

## Product workflow

Obital is designed around connected study journeys. A screen is successful when it naturally hands the user to the next useful action.

## Journey 1: account creation and unlock

```text
Choose account name
  -> create sign-in PIN
  -> confirm PIN
  -> create workspace encryption passkey
  -> explanation: PIN identifies; passkey decrypts
  -> enter the single account workspace
```

For a returning user, the same sequence becomes account name → PIN → workspace passkey. Errors must identify which credential failed without exposing sensitive details.

## Journey 2: source to curated lesson

```text
Docs: upload source
  -> extraction succeeds
  -> browse or search source contents
  -> mark passages or attach document with @
  -> Chat: ask a question or request a lesson
  -> Lessons: open generated curated notes
  -> complete checkpoint quiz
  -> Dashboard: review progress and XP
  -> Shop/rank: use earned progression
```

Docs contains user sources. Lessons contains AI-curated notes. The UI and README must use that distinction consistently.

## Journey 3: direct tutor question

```text
Open Chat
  -> type ordinary question
  -> request is sent immediately without browser model initialization
  -> receive Markdown-formatted streaming answer
  -> optionally turn the topic into a lesson or quiz
```

If the model fails, Chat shows a useful retry message and returns control without freezing or forcing a browser restart.

## Journey 4: focus session

```text
Workspace: choose duration and optional label
  -> start timer
  -> pause (elapsed segment is saved) or finish (final segment is saved)
  -> Today’s Focus updates
  -> Dashboard reflects accumulated focus
```

Reset is an explicit discard of the currently running unsaved segment. Previously saved paused segments remain in the daily total.

## Journey 5: social and leaderboard

```text
Open Friends
  -> search by username
  -> clear/edit without control movement
  -> add friend
  -> see presence
  -> compare progression in Ranks
```

Social features complement study progress; they must not block the private single-user study loop.

## Journey 6: cross-device continuation

```text
Device A: unlock -> Sync Push
  -> encrypted artifacts stored in cloud
Device B: sign in -> unlock with same workspace passkey -> Sync Pull
  -> continue chats, Docs, Lessons, rewards, and workspace state
```

## Failure and recovery workflow

| Failure | User experience | Recovery |
|---|---|---|
| Unsupported/empty upload | Clear validation message | Select a supported non-empty file |
| Indexing failure | Source remains readable/searchable | Retry “Refresh smart search” only if needed |
| Chat resource/provider failure | Stream ends with retryable message | Wait briefly and resend |
| Lesson generation failure | Job reports failed/rejected state | Adjust topic/source and retry |
| Expired unlock session | Protected action explains re-unlock | Sign in/unlock again |
| Sync unavailable | Local work remains intact | Retry when connected |

## Development workflow

1. Start from a reported user journey, not an isolated component.
2. Reproduce the problem and record the environment.
3. Trace frontend, API, service, storage, and external-provider boundaries.
4. Implement the smallest fix that improves the entire journey.
5. Add regression coverage at the lowest practical layer.
6. Run TypeScript compilation and the Vite production build.
7. Run targeted backend tests, then the compatible broader suite.
8. Exercise the journey in representative browsers/viewports.
9. Rebuild `backend/app/templates` so the served app matches source.
10. Update the six core documents when behavior or scope changes.

## Compatibility test missions

Give testers outcome-based missions rather than click instructions:

- “Create an account and explain why two credentials were requested.”
- “Upload a document, find a concept inside it, and ask the tutor about it.”
- “Turn that concept into a lesson and complete a quiz.”
- “Pause a focus timer and confirm your work was counted.”
- “Move your study state to another device.”
- “Try to break Chat with repeated messages, refreshes, network interruption, and large Markdown answers.”

Capture browser, OS, device, action, expected behavior, actual behavior, severity, and reproduction rate for every defect.

## Release checklist

- [ ] Core source → Chat → Lesson → Quiz → reward loop passes.
- [ ] PIN/passkey and Docs/Lessons distinctions are understood by new testers.
- [ ] Chrome, Edge, Firefox, and Safari smoke tests pass.
- [ ] Windows and macOS tests pass; Linux smoke test recorded.
- [ ] Desktop and representative mobile viewports pass.
- [ ] Keyboard-only primary flow passes.
- [ ] AI timeout, rate-limit, and malformed-output states recover.
- [ ] Two-device encrypted sync passes.
- [ ] Production bundle is rebuilt and served.
- [ ] No open P0 defects remain.

See [PLAN.md](PLAN.md) for prioritization and [PRD.md](PRD.md) for acceptance requirements.
