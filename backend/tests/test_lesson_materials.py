from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.api.common import require_account
from backend.app.api.lesson import (
    AddLessonMaterialRequest,
    SavedChatMessage,
    _build_lesson_material,
)
from backend.app.services.storage import _lesson_from_account_item
from backend.app.main import app
from engine.quiz import account_system
from engine.quiz.account_system import AccountStore


def quiz_payload() -> dict:
    return {
        "quiz_id": "quiz_graphs",
        "topic_filter": "graphs",
        "questions": [
            {
                "question_id": "q1",
                "topic_id": "graphs",
                "topic_title": "Graphs",
                "question": "Which traversal uses a queue?",
                "expected_answer": "BFS",
                "difficulty": "medium",
                "question_mode": "mcq",
                "options": ["DFS", "BFS", "A*", "Dijkstra"],
                "correct_option_index": 1,
            }
        ],
    }


def test_chat_material_strips_embedded_quiz_payload() -> None:
    request = AddLessonMaterialRequest(
        kind="chat",
        title="Graph review",
        messages=[
            SavedChatMessage(role="user", content="Explain BFS."),
            SavedChatMessage(
                role="assistant",
                content=(
                    "BFS explores level by level.\n\n"
                    "=== Quiz: BFS ===\n[QUIZ_DATA_START]{\"questions\": []}[QUIZ_DATA_END]"
                ),
            ),
        ],
    )

    material = _build_lesson_material(request)
    assert material["kind"] == "chat"
    assert "### You" in material["markdown"]
    assert "BFS explores level by level." in material["markdown"]
    assert "QUIZ_DATA_START" not in material["markdown"]


def test_material_fingerprint_is_stable_and_content_sensitive() -> None:
    first = _build_lesson_material(
        AddLessonMaterialRequest(
            kind="chat",
            messages=[SavedChatMessage(role="user", content="Explain trees")],
        )
    )
    renamed = _build_lesson_material(
        AddLessonMaterialRequest(
            kind="chat",
            title="A different display title",
            messages=[SavedChatMessage(role="user", content="Explain trees")],
        )
    )
    changed = _build_lesson_material(
        AddLessonMaterialRequest(
            kind="chat",
            messages=[SavedChatMessage(role="user", content="Explain graphs")],
        )
    )

    assert first["source_key"] == renamed["source_key"]
    assert first["source_key"] != changed["source_key"]


def test_request_rejects_mismatched_and_oversized_sources() -> None:
    with pytest.raises(ValidationError, match="cannot include a quiz"):
        AddLessonMaterialRequest(
            kind="chat",
            messages=[{"role": "user", "content": "hello"}],
            quiz=quiz_payload(),
        )
    with pytest.raises(ValidationError, match="at most 20"):
        payload = quiz_payload()
        payload["questions"] *= 21
        AddLessonMaterialRequest(kind="quiz", quiz=payload)


def test_store_adds_material_idempotently_without_changing_progress() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "accounts.json"
        with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
            store = AccountStore(path)
            store.create_user(username="alice", pin="1234")
            store.start_lesson(
                username="alice",
                lesson_id="lesson_graphs",
                title="Graphs",
                topic="graphs",
                checkpoints=[{"title": "Basics", "quiz": [{"question": "Q"}]}],
            )
            material = _build_lesson_material(
                AddLessonMaterialRequest(
                    kind="quiz",
                    title="BFS quiz",
                    quiz=quiz_payload(),
                )
            )

            first = store.add_lesson_material(
                username="alice",
                lesson_id="lesson_graphs",
                material=material,
            )
            duplicate = store.add_lesson_material(
                username="alice",
                lesson_id="lesson_graphs",
                material=material,
            )
            lesson = store.get_active_lesson(username="alice")

    assert first["created"] is True
    assert duplicate["created"] is False
    assert first["total_materials"] == duplicate["total_materials"] == 1
    assert lesson is not None
    assert lesson["current_checkpoint_index"] == 0
    assert len(lesson["checkpoints"]) == 1
    assert len(lesson["materials"]) == 1


def test_store_rejects_materials_over_two_megabytes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "accounts.json"
        with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
            store = AccountStore(path)
            store.create_user(username="alice", pin="1234")
            store.start_lesson(
                username="alice",
                lesson_id="lesson_graphs",
                title="Graphs",
                topic="graphs",
                checkpoints=[],
            )

            with pytest.raises(RuntimeError, match="cannot exceed 2 MB"):
                store.add_lesson_material(
                    username="alice",
                    lesson_id="lesson_graphs",
                    material={
                        "material_id": "material_large",
                        "source_key": "chat:large",
                        "kind": "chat",
                        "title": "Too large",
                        "created_at": "2026-07-17T00:00:00+00:00",
                        "markdown": "x" * 2_000_000,
                        "quiz": None,
                    },
                )


def test_material_api_is_account_scoped_and_returns_duplicate_status() -> None:
    fake_store = mock.Mock()
    fake_store.add_lesson_material.side_effect = [
        {
            "lesson_id": "lesson_graphs",
            "material": {
                "material_id": "material_123",
                "kind": "chat",
            },
            "created": True,
            "total_materials": 1,
        },
        {
            "lesson_id": "lesson_graphs",
            "material": {
                "material_id": "material_123",
                "kind": "chat",
            },
            "created": False,
            "total_materials": 1,
        },
    ]
    app.dependency_overrides[require_account] = lambda: ("alice", b"dek")
    try:
        with mock.patch("backend.app.api.lesson._account_store", return_value=fake_store):
            client = TestClient(app)
            body = {
                "kind": "chat",
                "title": "Review",
                "messages": [{"role": "user", "content": "Explain graphs"}],
            }
            first = client.post("/lessons/lesson_graphs/materials", json=body)
            duplicate = client.post("/lessons/lesson_graphs/materials", json=body)
    finally:
        app.dependency_overrides.pop(require_account, None)

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    fake_store.add_lesson_material.assert_called_with(
        username="alice",
        lesson_id="lesson_graphs",
        material=mock.ANY,
        user_dek=b"dek",
    )


def test_material_api_returns_not_found_for_another_accounts_lesson() -> None:
    fake_store = mock.Mock()
    fake_store.add_lesson_material.side_effect = RuntimeError("Lesson not found.")
    app.dependency_overrides[require_account] = lambda: ("alice", None)
    try:
        with mock.patch("backend.app.api.lesson._account_store", return_value=fake_store):
            response = TestClient(app).post(
                "/lessons/lesson_owned_by_bob/materials",
                json={
                    "kind": "chat",
                    "messages": [{"role": "user", "content": "private"}],
                },
            )
    finally:
        app.dependency_overrides.pop(require_account, None)

    assert response.status_code == 404


def test_lesson_summaries_keep_material_bodies_out_of_list_payloads() -> None:
    lesson = _lesson_from_account_item(
        {
            "lesson_id": "lesson_graphs",
            "title": "Graphs",
            "status": "in_progress",
            "current_checkpoint": 0,
            "total_checkpoints": 1,
        },
        {
            "lesson_graphs": {
                "materials": [
                    {
                        "material_id": "material_1",
                        "kind": "chat",
                        "title": "Review",
                        "created_at": "2026-07-17T00:00:00+00:00",
                        "markdown": "private transcript",
                        "quiz": None,
                        "source_key": "chat:abc",
                    }
                ]
            }
        },
    )

    assert [material.model_dump(exclude_none=True) for material in lesson.materials] == [
        {
            "material_id": "material_1",
            "kind": "chat",
            "title": "Review",
            "created_at": "2026-07-17T00:00:00+00:00",
        }
    ]


def test_material_detail_returns_body_only_for_owned_lesson() -> None:
    fake_store = mock.Mock()
    fake_store.get_lesson_progress.return_value = {
        "lessons": {
            "lesson_graphs": {
                "materials": [
                    {
                        "material_id": "material_1",
                        "kind": "chat",
                        "title": "Review",
                        "created_at": "2026-07-17T00:00:00+00:00",
                        "markdown": "private transcript",
                        "quiz": None,
                    }
                ]
            }
        }
    }
    app.dependency_overrides[require_account] = lambda: ("alice", b"dek")
    try:
        with mock.patch("backend.app.api.lesson._account_store", return_value=fake_store):
            response = TestClient(app).get("/lessons/lesson_graphs/materials/material_1")
    finally:
        app.dependency_overrides.pop(require_account, None)

    assert response.status_code == 200
    assert response.json()["markdown"] == "private transcript"
    fake_store.get_lesson_progress.assert_called_once_with(username="alice", user_dek=b"dek")
