# Obital Testing Evidence

Feature changes locally verified: **10 August 2026**, macOS arm64, Python 3.14 and Node 26. CI runs the same groups on Python 3.13 and Node 22 (see [`.python-version`](../../.python-version) and the workflow files). The Playwright Chromium group runs on every pull request and was last verified in CI on the same date.

This document turns the testing claims in the README into executable evidence. The test files and named cases below are committed in the repository and the same stable groups run in [Core Quality CI](../../.github/workflows/quality-tests.yml).

## Verified baseline

| Layer | Command | Verified result |
|---|---|---:|
| Unit | `python -m pytest engine/tests/test_quiz_modes.py engine/tests/test_checkpoint_normalization.py backend/tests/test_auth_api.py backend/tests/test_stateless_storage.py -q` | **44 passed** |
| API/integration | `python -m pytest backend/tests/test_sync_api.py backend/tests/test_workspace_api.py backend/tests/test_stream_and_routes.py backend/tests/test_lesson_materials.py -q` | **40 passed** |
| Browser runtime | `python -m pytest backend/tests/test_browser_js.py -q` | **40 passed** |
| Frontend build | `npm run build` in `frontend/` | TypeScript + Vite build passed |

These are stable evidence groups, not a claim that every historical test in the repository currently passes. Some older tests target removed/deprecated symbols or platform-specific path behavior and are tracked as baseline debt. New regressions should not be hidden by that broader baseline, which is why the quality workflow runs explicit maintained groups.

## Unit-test cases

Unit tests isolate deterministic behavior and replace storage, database, or provider boundaries with fixtures/mocks where needed.

| Test case | File | Input/condition | Expected assertion |
|---|---|---|---|
| `test_mcq_build_and_grade_letter` | [`engine/tests/test_quiz_modes.py`](../../engine/tests/test_quiz_modes.py) | Build an MCQ and submit its correct letter | Question is marked correct and scored fully |
| `test_open_answer_typo_is_accepted` | [`engine/tests/test_quiz_modes.py`](../../engine/tests/test_quiz_modes.py) | Submit a close typo for an open answer | Typo-tolerant grading accepts the response |
| `test_invalid_question_mode_defaults_to_mcq` | [`engine/tests/test_checkpoint_normalization.py`](../../engine/tests/test_checkpoint_normalization.py) | Normalize an unsupported mode | Safe `mcq` default is produced |
| `test_unlock_token_is_bound_to_client_context` | [`backend/tests/test_auth_api.py`](../../backend/tests/test_auth_api.py) | Resolve one unlock token from another client | Token is rejected outside its client binding |
| `test_unlock_invalidates_when_version_changes` | [`backend/tests/test_auth_api.py`](../../backend/tests/test_auth_api.py) | Increment the account unlock version | Previously issued token becomes invalid |
| `test_stateless_account_store` | [`backend/tests/test_stateless_storage.py`](../../backend/tests/test_stateless_storage.py) | Read/write an account through stateless storage | Account data round-trips under account scope |
| `test_account_store_preserves_encrypted_progress_on_wrong_dek` | [`backend/tests/test_stateless_storage.py`](../../backend/tests/test_stateless_storage.py) | Read encrypted progress with the wrong key | Existing encrypted progress is not overwritten |
| `test_delete_doc_invalidates_cached_document` | [`backend/tests/test_stateless_storage.py`](../../backend/tests/test_stateless_storage.py) | Delete a previously cached document | Deleted data cannot be returned from stale cache |

Run one case directly:

```powershell
python -m pytest backend/tests/test_auth_api.py::AuthApiTests::test_unlock_token_is_bound_to_client_context -v
```

## API and integration-test cases

Integration tests cross at least one application boundary: HTTP → service, encryption → storage, or local account → sync behavior.

| Test case | File | Boundaries exercised | Expected assertion |
|---|---|---|---|
| `test_sync_push_rejects_missing_unlock_token` | [`backend/tests/test_sync_api.py`](../../backend/tests/test_sync_api.py) | HTTP auth dependency → sync API | Protected sync rejects an unauthenticated request |
| `test_resolve_dek_from_passphrase_enforces_lockout` | [`backend/tests/test_sync_api.py`](../../backend/tests/test_sync_api.py) | Passphrase verification → lockout persistence | Repeated failures enforce lockout |
| `test_workspace_encrypted_at_rest_and_decrypts` | [`backend/tests/test_workspace_api.py`](../../backend/tests/test_workspace_api.py) | Workspace API → encrypted artifact storage | Stored payload is ciphertext and reads back correctly |
| `test_put_then_get_roundtrip` | [`backend/tests/test_workspace_api.py`](../../backend/tests/test_workspace_api.py) | HTTP request → workspace service → response | Workspace state round-trips |
| `test_chat_messages_stream_exists` | [`backend/tests/test_stream_and_routes.py`](../../backend/tests/test_stream_and_routes.py) | FastAPI routing → streaming service | Chat streaming endpoint is reachable |
| `test_lesson_grade_rejects_negative_checkpoint_index` | [`backend/tests/test_stream_and_routes.py`](../../backend/tests/test_stream_and_routes.py) | Validation → lesson grading service | Invalid index is rejected before grading/mutation |
| `test_rejects_wrong_vector_length` | [`backend/tests/test_stream_and_routes.py`](../../backend/tests/test_stream_and_routes.py) | Pydantic request validation → Docs API | Wrong embedding dimension returns validation error |
| `test_chat_material_strips_embedded_quiz_payload` | [`backend/tests/test_lesson_materials.py`](../../backend/tests/test_lesson_materials.py) | Chat payload → lesson-material formatter | Transcript remains readable without duplicating hidden quiz JSON |
| `test_store_adds_material_idempotently_without_changing_progress` | [`backend/tests/test_lesson_materials.py`](../../backend/tests/test_lesson_materials.py) | Material builder → account store → lesson state | Identical saves create one resource and leave checkpoint progress untouched |
| `test_store_rejects_materials_over_two_megabytes` | [`backend/tests/test_lesson_materials.py`](../../backend/tests/test_lesson_materials.py) | Account store → serialized lesson resources | Oversized aggregate is rejected before persistence |
| `test_material_api_is_account_scoped_and_returns_duplicate_status` | [`backend/tests/test_lesson_materials.py`](../../backend/tests/test_lesson_materials.py) | Auth dependency → HTTP route → encrypted account store | Authenticated account and DEK are forwarded; duplicate status is explicit |
| `test_material_api_returns_not_found_for_another_accounts_lesson` | [`backend/tests/test_lesson_materials.py`](../../backend/tests/test_lesson_materials.py) | Authenticated HTTP route → account-scoped lookup | Another account's Lesson cannot be used as a destination |

## Browser-runtime and UI JavaScript tests

[`backend/tests/test_browser_js.py`](../../backend/tests/test_browser_js.py) contains **40 Playwright cases** executed in real headless Chromium.

### What is actually covered

- 37 cases execute grading and vector JavaScript inside a real Chromium page: tokenization, edit distance, typo thresholds, lexical scoring, cosine similarity, MCQ matching, and open-answer grading.
- 3 cases start the FastAPI app, navigate Chromium to the live application, intercept pipeline HTTP calls with `page.route()`, and verify curriculum → topic-QA → save ordering plus failure short-circuiting.

Representative cases:

| Test case | Runtime behavior |
|---|---|
| `TestCosineSimilarity::test_zero_vector_returns_zero` | Confirms the browser implementation avoids division-by-zero/NaN |
| `TestMcqGrading::test_correct_text_case_insensitive` | Confirms case-insensitive UI grading behavior |
| `TestOpenAnswerGrading::test_semantic_blend_boostes_score` | Confirms lexical and semantic scores combine correctly |
| `TestPipelineOrchestration::test_pipeline_generate_calls_three_endpoints` | Confirms the browser performs curriculum, per-topic QA, then save in order |
| `TestPipelineOrchestration::test_pipeline_curriculum_failure_stops_flow` | Confirms downstream work does not continue after a curriculum failure |

### What is not claimed

This suite is browser-runtime logic and mocked pipeline integration coverage. It is **not** a complete visual end-to-end suite for every React screen, hover state, responsive breakpoint, or real AI-provider response. Those journeys are covered by the manual system and user-testing records below until dedicated React component/E2E coverage is added.

Run it with:

```powershell
pip install -r backend/tests/requirements-test.in
python -m playwright install chromium
python -m pytest backend/tests/test_browser_js.py -v
```

## Regression testing

A regression test is added or selected whenever a defect exposes a reusable invariant. The test must fail for the broken behavior and pass after the fix. Maintained regression cases run on every pull request in Core Quality CI.

| Regression protected | Named test | Prevents |
|---|---|---|
| Resource errors are user-facing and retryable | `test_os_error_is_reported_as_retryable_resource_error` in [`backend/tests/test_stream_and_routes.py`](../../backend/tests/test_stream_and_routes.py) | Leaking `Internal server error: OSError` into Chat |
| Unlock tokens expire through version changes | `test_unlock_invalidates_when_version_changes` | Old sessions remaining valid after lock/logout |
| Encrypted progress survives the wrong key | `test_account_store_preserves_encrypted_progress_on_wrong_dek` | Accidental ciphertext overwrite/data loss |
| Deleted Docs leave no stale cache entry | `test_delete_doc_invalidates_cached_document` | Deleted material reappearing |
| Invalid checkpoint indexes cannot mutate progress | `test_lesson_grade_rejects_negative_checkpoint_index` | Python negative indexing grading the wrong checkpoint |
| Pipeline failure stops later calls | the two pipeline failure cases in `test_browser_js.py` | Saving partial/invalid generated artifacts |
| Saved study material cannot advance a Lesson | `test_store_adds_material_idempotently_without_changing_progress` | Chat/Quiz saves corrupting checkpoint progression |
| Repeated saves do not create duplicate material | `test_store_adds_material_idempotently_without_changing_progress` | Accidental duplicate content and unbounded growth |
| A user cannot save into another account's Lesson | `test_material_api_returns_not_found_for_another_accounts_lesson` | Cross-account material injection |
| Saved material has a bounded aggregate size | `test_store_rejects_materials_over_two_megabytes` | Oversized encrypted profile records and avoidable memory pressure |

For frontend fixes that do not yet have a component test harness, TypeScript compilation, production build, Chromium runtime tests, and the relevant manual mission are required. Adding React component tests is recorded as a future coverage improvement rather than implied as existing evidence.

## Manual system and user testing

- [`docs/testing/MS3_USER_TESTING.md`](MS3_USER_TESTING.md) records the external MS3 user-testing session, findings, fixes, and retest status.
- [`docs/testing/MANUAL_SYSTEM_TESTS.md`](MANUAL_SYSTEM_TESTS.md) provides reproducible smoke cases and an execution-log template.

Manual testing covers real behaviors that mocked automation does not: onboarding comprehension, visual flow, browser responsiveness, perceived freezes, cross-device sync, and feature discoverability.

## Code-review evidence

Design debt and risk decisions remain visible in [`CODE_REVIEW.md`](../reviews/CODE_REVIEW.md), [`CODE_REVIEW_2.md`](../reviews/CODE_REVIEW_2.md), [`CODE_REVIEW_3.md`](../reviews/CODE_REVIEW_3.md), [`CODE_REVIEW_4.md`](../reviews/CODE_REVIEW_4.md), [`CODE_REVIEW_LESSON_MATERIALS.md`](../reviews/CODE_REVIEW_LESSON_MATERIALS.md), and [`REVIEW_SUMMARY.md`](../reviews/REVIEW_SUMMARY.md). Test evidence does not replace review; review identifies risks, while tests provide repeatable checks for selected behavior.
