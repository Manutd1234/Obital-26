from __future__ import annotations

import os
import unittest
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.schemas import ChatSession, Doc, DocChunk, Lesson
from backend.app.services import settings_service
from backend.app.api.common import require_account


def _mock_require_account():
    return ("admin", None)


class ChatApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[require_account] = _mock_require_account
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(require_account, None)

    def test_send_message_rejects_whitespace_only_message(self) -> None:
        with mock.patch("backend.app.services.chat_service.get_session", return_value=ChatSession(id="session_1")):
            resp = self.client.post(
                "/chat/sessions/session_1/messages",
                params={"account_id": "admin"},
                json={"message": "   "},
            )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "Message must not be empty")

    def test_send_message_rejects_unknown_lesson(self) -> None:
        with mock.patch("backend.app.services.chat_service.get_session", return_value=ChatSession(id="session_1")):
            with mock.patch("backend.app.services.chat_service.get_lesson", return_value=None):
                resp = self.client.post(
                    "/chat/sessions/session_1/messages",
                    params={"account_id": "admin"},
                    json={"message": "hello", "lesson_id": "missing_lesson"},
                )

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"], "Lesson not found")

    def test_send_message_success_returns_reply(self) -> None:
        with mock.patch("backend.app.services.chat_service.get_session", return_value=ChatSession(id="session_1")):
            with mock.patch(
                "backend.app.services.chat_service.get_lesson",
                return_value=Lesson(id="lesson_trees", title="Trees", objective="", content_blocks=[]),
            ):
                with mock.patch("backend.app.services.chat_service.get_doc_texts_for_chunk_ids", return_value=[]):
                    with mock.patch("backend.app.services.chat_service.generate_reply", return_value="assistant reply"):
                        with mock.patch("backend.app.services.chat_service.append_message"):
                            resp = self.client.post(
                                "/chat/sessions/session_1/messages",
                                params={"account_id": "admin"},
                                json={"message": "hello", "lesson_id": "lesson_trees"},
                            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["session_id"], "session_1")
        self.assertEqual(resp.json()["reply"], "assistant reply")

    def test_send_message_resolves_quoted_doc_mention_without_chunk_ids(self) -> None:
        doc = Doc(
            id="upload_algebra_notes_abc",
            name="Algebra Notes.md",
            uploaded_at=datetime.now(UTC),
            chunks=[DocChunk(chunk_id="upload_algebra_notes_abc_chunk_1", text="Quadratics use ax^2 + bx + c.")],
            chunk_count=1,
        )
        captured: dict[str, object] = {}

        def fake_generate_reply(**kwargs):
            captured["doc_texts"] = kwargs["doc_texts"]
            return "assistant reply"

        with mock.patch("backend.app.services.chat_service.get_session", return_value=ChatSession(id="session_1")):
            with mock.patch("backend.app.services.chat_service.list_docs", return_value=[doc]):
                with mock.patch(
                    "backend.app.services.chat_service.get_doc_texts_for_chunk_ids",
                    return_value=["Quadratics use ax^2 + bx + c."],
                ) as get_texts:
                    with mock.patch("backend.app.services.chat_service.generate_reply", side_effect=fake_generate_reply):
                        with mock.patch("backend.app.services.chat_service.append_message"):
                            resp = self.client.post(
                                "/chat/sessions/session_1/messages",
                                params={"account_id": "admin"},
                                json={"message": 'summarize @"Algebra Notes.md"'},
                            )

        self.assertEqual(resp.status_code, 200)
        get_texts.assert_called_once_with(
            ["upload_algebra_notes_abc_chunk_1"],
            account_id="admin",
            user_dek=None,
        )
        self.assertEqual(captured["doc_texts"], ["Quadratics use ax^2 + bx + c."])

    def test_send_message_settings_tool_updates_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch.object(settings_service, "BASE_DIR", base):
                with mock.patch.object(settings_service, "_is_db_storage_mode", return_value=False):
                    with mock.patch("backend.app.services.chat_service.get_session", return_value=ChatSession(id="session_1")):
                        with mock.patch("backend.app.services.chat_service.append_message"):
                            resp = self.client.post(
                                "/chat/sessions/session_1/messages",
                                params={"account_id": "admin"},
                                json={"message": "/settings set system_prompt You are strict."},
                            )

                prompt_path = (
                    base / "engine" / "accounts" / "admin" / "config" / "chat_system_prompt.md"
                )
                prompt_exists = prompt_path.exists()

        self.assertEqual(resp.status_code, 200)
        self.assertIn("System prompt updated.", resp.json()["reply"])
        self.assertTrue(prompt_exists)

    def test_get_session_invalid_unlock_token_returns_401(self) -> None:
        fake_row = mock.Mock(
            unlock_version=1,
            locked_until=None,
            failed_unlock_attempts=0,
            consecutive_lockouts=0,
        )
        fake_query = mock.Mock()
        fake_query.with_for_update.return_value = fake_query
        fake_query.where.return_value = fake_query
        fake_query.where.return_value.first.return_value = fake_row

        class _FakeSessionFactory:
            def __call__(self):
                class _Ctx:
                    def __enter__(self_inner):
                        db = mock.Mock()
                        db.query.return_value = fake_query
                        return db

                    def __exit__(self_inner, exc_type, exc, tb):
                        return False

                return _Ctx()

        app.dependency_overrides.pop(require_account, None)
        with mock.patch.dict(os.environ, {"OBITAL_LOCAL_SINGLE_USER": ""}, clear=False):
            with mock.patch("backend.app.api.common.db_enabled", return_value=True):
                with mock.patch("backend.app.api.common.SessionLocal", new=_FakeSessionFactory()):
                    resp = self.client.get(
                        "/chat/sessions/session_1",
                        params={"account_id": "admin"},
                        headers={"X-Unlock-Token": "bad-token"},
                    )

        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "Invalid or expired unlock token")


if __name__ == "__main__":
    unittest.main()
