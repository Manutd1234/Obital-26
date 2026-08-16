# Obital Data Processing Flow

## Purpose

This document follows user data from upload or input to a study outcome. It is the operational companion to [ARCHITECTURE.md](ARCHITECTURE.md): architecture describes the components, while this document describes how information moves between them.

## Core flow

```text
User signs in and unlocks workspace
  -> uploads a source document
  -> backend validates and extracts text
  -> text is divided into addressable passages
  -> passages and optional semantic vectors are stored per account
  -> user searches, marks, or attaches relevant material
  -> Chat answers from that selected context
  -> lesson harness creates structured study notes and embedded quizzes
  -> quiz grading records progress, XP, rank, and shop currency
  -> optional encrypted sync makes the same workspace available on another device
```

## 1. Identity and decryption boundary

The sign-in PIN and workspace passkey have separate responsibilities:

1. The PIN verifies the account identity.
2. The workspace passkey derives the key used to decrypt private study data.
3. An unlock session is bound to the account and client ID.
4. Each account owns one encrypted workspace; users do not manage multiple vaults.

The frontend sends `X-Unlock-Token` and `X-Client-Id` with protected API requests. Plaintext study content is only available after the unlock boundary has been crossed.

## 2. Document ingestion

`POST /docs/upload` accepts supported study material and applies these stages:

| Stage | Responsibility | Primary code |
|---|---|---|
| Validate | Enforce file and request-size limits | `backend/app/main.py`, `backend/app/api/doc.py` |
| Extract | Convert PDF/text-like input into normalized text | `backend/app/services/storage.py`, `engine/extract_text.py` |
| Chunk | Split content into stable passages with IDs | `backend/app/services/storage.py` |
| Persist | Store account-scoped document metadata and content | `backend/app/services/storage.py` |
| Index | Generate optional MiniLM vectors in the configured embedding mode | `frontend/src/workers/embedding.worker.ts`, `backend/app/services/embeddings.py` |

Uploads succeed independently of semantic indexing. If vector generation fails, the source is still available for reading, filename/content search, and explicit passage attachment.

## 3. Search and source selection

The Docs screen is the source library, not the AI-notes screen.

- **Text search** searches filenames and extracted contents through `GET /docs/search`.
- **Smart search** uses stored vectors when a deployment enables semantic indexing.
- **Marked passages** are explicit chunk IDs selected by the user.
- **`@` attachment** resolves a document and sends its passages with the next chat message.

Ordinary chat does not initialize the large browser embedding model. This keeps general questions responsive and prevents browser memory spikes. Users opt into document grounding by attaching a document or marking passages.

## 4. Chat processing

```text
ChatInput
  -> POST /chat/sessions/{session_id}/messages/stream
  -> validate session and persist user message
  -> resolve selected document passages
  -> fast-path command router (quiz, lesson, dashboard, docs)
  -> LLM agent and approved study tools
  -> stream SSE events to the React client
  -> render Markdown and persist the completed assistant message
```

The stream emits tokens plus structured progress events for tools, lesson-generation work, confirmations, artifacts, and errors. UI updates are batched to reduce rendering pressure during long Markdown answers.

## 5. Curated AI notes, lessons, and quizzes

Curated AI notes live in **Lessons**:

1. Chat receives a request such as “create a lesson about binary trees.”
2. The lesson harness retrieves selected/account sources, with a controlled fallback when necessary.
3. A planner creates the checkpoint structure.
4. Bounded workers generate lesson Markdown, source metadata, and quiz questions.
5. A reviewer validates completeness and requests regeneration when required.
6. The lesson is saved and surfaced in Lessons and Dashboard.
7. Quiz answers are graded and applied to progress and rewards.

## 6. Progress, focus, and rewards

Quiz results update account-scoped reward events. The Dashboard reads those events to show points, accuracy, attempts, and progression. Workspace timer segments are recorded when the timer is paused or completed, so partial focus work counts toward Today’s Focus.

This creates one connected loop:

```text
Source -> Chat -> Lesson -> Quiz -> XP/rank -> Shop -> continued study
```

## 7. Sync flow

Sync is optional and encrypted:

- **Push** encrypts eligible local artifacts and updates cloud storage.
- **Pull** retrieves account artifacts, decrypts them with the active workspace key, and merges them locally.
- Sync never makes the sign-in PIN a decryption key.
- Failures are reported without discarding the local source of truth.

See [SECURITY.md](../../SECURITY.md) for the threat model and [workflow.md](../planning/workflow.md) for end-to-end user journeys.
