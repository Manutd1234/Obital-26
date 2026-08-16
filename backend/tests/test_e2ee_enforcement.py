"""Tests that E2EE writers enforce DEK presence in non-SQLite mode.

Regression tests for §5.3 — "E2EE silently degrades to plaintext silently."
All writers must raise ValueError when user_dek is absent outside SQLite mode,
instead of silently storing plaintext.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.services.crypto_local import (
    decrypt_text,
    encrypt_text,
    generate_dek,
    require_dek,
)


class TestRequireDek(unittest.TestCase):
    def test_require_dek_returns_dek_when_present(self) -> None:
        dek = generate_dek()
        result = require_dek(dek, "test context")
        self.assertIs(result, dek)

    def test_require_dek_raises_when_none(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            require_dek(None, "test context")
        self.assertIn("user_dek is required", str(ctx.exception))
        self.assertIn("test context", str(ctx.exception))


class TestDecryptTextPassthroughWarning(unittest.TestCase):
    def test_plaintext_passthrough_logs_warning(self) -> None:
        dek = generate_dek()
        with self.assertLogs("backend.app.services.crypto_local", level="WARNING") as cm:
            result = decrypt_text(dek, "plain text without prefix")
        self.assertEqual(result, "plain text without prefix")
        self.assertTrue(any("plaintext passthrough" in msg for msg in cm.output))

    def test_encrypted_text_decrypts_normally(self) -> None:
        dek = generate_dek()
        plaintext = "hello world"
        encrypted = encrypt_text(dek, plaintext)
        result = decrypt_text(dek, encrypted)
        self.assertEqual(result, plaintext)

    def test_no_warning_when_dek_is_none(self) -> None:
        with self.assertNoLogs("backend.app.services.crypto_local", level="WARNING"):
            result = decrypt_text(None, "plain text")
        self.assertEqual(result, "plain text")

    def test_encrypted_passthrough_with_wrong_prefix(self) -> None:
        dek = generate_dek()
        with self.assertLogs("backend.app.services.crypto_local", level="WARNING"):
            result = decrypt_text(dek, "other:v1:something")
        self.assertEqual(result, "other:v1:something")


def _make_qa_output():
    """Create a minimal CurriculumQAOutput for testing."""
    from engine.pipeline.models import (
        Curriculum,
        CurriculumQAOutput,
        Topic,
    )

    return CurriculumQAOutput(
        curriculum=Curriculum(
            title="Test",
            description="Test curriculum",
            topics=[Topic(id="t1", title="Topic 1", summary="Summary 1")],
        ),
        qa_sets=[],
    )


class TestChatToolsQaWriterEnforcement(unittest.TestCase):
    """_save_qa_to_db must raise when user_dek is None in stateless DB mode."""

    def test_raises_when_dek_missing(self) -> None:
        from backend.app.services.chat_tools import _save_qa_to_db

        qa = _make_qa_output()

        with mock.patch("backend.app.services.chat_tools.db_service") as mock_db:
            mock_db.is_stateless_db_mode.return_value = True
            with self.assertRaises(ValueError) as ctx:
                _save_qa_to_db("admin", qa, user_dek=None)
            self.assertIn("user_dek is required", str(ctx.exception))

    def test_noop_when_not_stateless_mode(self) -> None:
        from backend.app.services.chat_tools import _save_qa_to_db

        qa = _make_qa_output()

        with mock.patch("backend.app.services.chat_tools.db_service") as mock_db:
            mock_db.is_stateless_db_mode.return_value = False
            _save_qa_to_db("admin", qa, user_dek=None)
            mock_db.SessionLocal.assert_not_called()


class TestPipelineUpsertArtifactEnforcement(unittest.TestCase):
    """_upsert_artifact must raise when user_dek is None."""

    def test_raises_when_dek_missing(self) -> None:
        from backend.app.services.models import AccountArtifactModel

        mock_session = mock.MagicMock()
        mock_session.query.return_value.where.return_value.first.return_value = None

        with mock.patch(
            "backend.app.services.models.AccountArtifactModel", AccountArtifactModel
        ):
            # _upsert_artifact imports AccountArtifactModel lazily inside the function.
            # We can test the require_dek guard directly by calling with None.
            from backend.app.api.pipeline import _upsert_artifact

            with self.assertRaises(ValueError) as ctx:
                _upsert_artifact(
                    mock_session,
                    account_id="admin",
                    relative_path="test.json",
                    content_bytes=b"test",
                    user_dek=None,
                )
            self.assertIn("user_dek is required", str(ctx.exception))

    def test_encrypts_when_dek_present(self) -> None:
        from backend.app.api.pipeline import _upsert_artifact

        dek = generate_dek()

        mock_session = mock.MagicMock()
        mock_session.query.return_value.where.return_value.first.return_value = None

        # _upsert_artifact imports AccountArtifactModel lazily; patch it at
        # the import location so the mock is used.
        with mock.patch("backend.app.services.models.AccountArtifactModel"):
            _upsert_artifact(
                mock_session,
                account_id="admin",
                relative_path="test.json",
                content_bytes=b"test content",
                user_dek=dek,
            )
            mock_session.add.assert_called_once()
            stored = mock_session.add.call_args[0][0].content
            self.assertNotEqual(stored, b"test content")


class TestAccountSyncWriterEnforcement(unittest.TestCase):
    """account_sync writers must raise when user_dek is None for cloud writes."""

    def test_chat_writer_raises_when_dek_missing(self) -> None:
        from backend.app.services.account_sync import _write_chat_sessions_to_db
        from backend.app.schemas import ChatMessage, ChatSession

        session = ChatSession(
            id="test-session",
            created_at="2026-01-01T00:00:00Z",
            messages=[
                ChatMessage(
                    role="user",
                    text="hello",
                    created_at="2026-01-01T00:00:00Z",
                )
            ],
        )

        mock_db = mock.MagicMock()
        mock_db.query.return_value.where.return_value.all.return_value = []

        with self.assertRaises(ValueError) as ctx:
            _write_chat_sessions_to_db(mock_db, "admin", [session], user_dek=None)
        self.assertIn("user_dek is required", str(ctx.exception))

    def test_reward_writer_from_files_empty_is_fine(self) -> None:
        from backend.app.services.account_sync import _write_reward_sessions_from_files

        mock_db = mock.MagicMock()
        mock_db.query.return_value.where.return_value.all.return_value = []

        result = _write_reward_sessions_from_files(mock_db, "admin", [], user_dek=None)
        self.assertEqual(result, 0)

    def test_reward_writer_from_files_raises_with_actual_data(self) -> None:
        from backend.app.services.account_sync import _write_reward_sessions_from_files

        mock_db = mock.MagicMock()
        mock_db.query.return_value.where.return_value.all.return_value = []

        with tempfile.TemporaryDirectory() as td:
            reward_file = Path(td) / "reward_session_1.jsonl"
            reward_file.write_text(
                json.dumps({
                    "event": "start",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "total_points": 10,
                    "total_quizzes": 1,
                    "total_correct": 1,
                    "total_answered": 1,
                    "note": "test",
                }) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as ctx:
                _write_reward_sessions_from_files(mock_db, "admin", [reward_file], user_dek=None)
            self.assertIn("user_dek is required", str(ctx.exception))


class TestStorageWriterEnforcement(unittest.TestCase):
    """storage.py writers must raise when user_dek is None in non-SQLite mode."""

    def test_append_message_raises_when_dek_missing(self) -> None:
        from backend.app.services.storage import append_message
        from backend.app.schemas import ChatMessage

        msg = ChatMessage(role="user", text="hello", created_at="2026-01-01T00:00:00Z")

        with mock.patch("backend.app.services.storage.db_enabled", return_value=True), \
             mock.patch("backend.app.services.storage.SessionLocal") as MockSession, \
             mock.patch("backend.app.services.storage.is_sqlite_mode", return_value=False):
            mock_db = mock.MagicMock()
            mock_db.query.return_value.filter.return_value.first.return_value = mock.MagicMock()
            mock_db.query.return_options.return_value.filter.return_value.first.return_value = mock.MagicMock()
            mock_session_ctx = mock.MagicMock()
            mock_session_ctx.__enter__ = mock.MagicMock(return_value=mock_db)
            mock_session_ctx.__exit__ = mock.MagicMock(return_value=False)
            MockSession.return_value = mock_session_ctx

            # The function calls db.query(ChatSessionModel).where(...).first()
            # We need to make sure it doesn't find a session (returns None)
            # so it falls through to the raise.
            mock_db.query.return_value.where.return_value.first.return_value = None

            with self.assertRaises(ValueError) as ctx:
                append_message("session-1", msg, account_id="admin", user_dek=None)
            self.assertIn("user_dek is required", str(ctx.exception))

    def test_append_reward_event_raises_when_dek_missing(self) -> None:
        from backend.app.services.storage import append_reward_session_log

        with mock.patch("backend.app.services.storage.db_enabled", return_value=True), \
             mock.patch("backend.app.services.storage.SessionLocal") as MockSession, \
             mock.patch("backend.app.services.storage._safe_rewards_summary", return_value={"total_points": 0, "total_quizzes": 0, "total_correct": 0, "total_answered": 0}):
            mock_db = mock.MagicMock()
            mock_db.query.return_value.where.return_value.first.return_value = mock.MagicMock()
            mock_session_ctx = mock.MagicMock()
            mock_session_ctx.__enter__ = mock.MagicMock(return_value=mock_db)
            mock_session_ctx.__exit__ = mock.MagicMock(return_value=False)
            MockSession.return_value = mock_session_ctx

            with self.assertRaises(ValueError) as ctx:
                append_reward_session_log("admin", "session-1", note="test", user_dek=None)
            self.assertIn("user_dek is required", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
