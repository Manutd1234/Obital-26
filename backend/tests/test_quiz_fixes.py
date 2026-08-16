from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from unittest import mock

from backend.app.services.crypto_local import generate_dek
from engine.pipeline.models import CurriculumQAOutput, Curriculum, TopicQASet, Topic, QuestionAnswer


def _make_qa_output(topic_title: str = "Binary Trees") -> CurriculumQAOutput:
    """Create a minimal CurriculumQAOutput for testing."""
    return CurriculumQAOutput(
        curriculum=Curriculum(
            title="Test Curriculum",
            description="Test",
            topics=[
                Topic(id="t1", title=topic_title, summary="Summary", prerequisites=[])
            ],
        ),
        qa_sets=[
            TopicQASet(
                topic_id="t1",
                topic_title=topic_title,
                questions=[
                    QuestionAnswer(
                        question="What is a binary tree?",
                        answer="A tree with at most two children.",
                        difficulty="easy",
                        source_context="CS basics",
                    )
                ],
            )
        ],
    )


class SaveQaToDbTests(unittest.TestCase):
    """Tests for _save_qa_to_db in chat_tools.py."""

    @mock.patch("backend.app.services.chat_tools.db_service")
    def test_saves_new_qa_to_db(self, mock_db_service) -> None:
        mock_db_service.is_stateless_db_mode.return_value = True
        mock_db_service.SessionLocal = mock.MagicMock()
        mock_session = mock_db_service.SessionLocal.return_value.__enter__.return_value
        mock_session.query.return_value.where.return_value.first.return_value = None

        from backend.app.services.chat_tools import _save_qa_to_db
        qa = _make_qa_output()
        dek = generate_dek()
        _save_qa_to_db("testuser", qa, user_dek=dek)

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        added_artifact = mock_session.add.call_args[0][0]
        self.assertEqual(added_artifact.account_id, "testuser")
        self.assertEqual(added_artifact.relative_path, "output/output_curriculum_qa.json")
        self.assertNotEqual(added_artifact.content, json.dumps(qa.model_dump()).encode("utf-8"))

    @mock.patch("backend.app.services.chat_tools.db_service")
    def test_updates_existing_qa_in_db(self, mock_db_service) -> None:
        mock_db_service.is_stateless_db_mode.return_value = True
        mock_db_service.SessionLocal = mock.MagicMock()
        mock_session = mock_db_service.SessionLocal.return_value.__enter__.return_value
        existing_row = mock.Mock()
        mock_session.query.return_value.where.return_value.first.return_value = existing_row

        from backend.app.services.chat_tools import _save_qa_to_db
        qa = _make_qa_output()
        dek = generate_dek()
        _save_qa_to_db("testuser", qa, user_dek=dek)

        self.assertIsNotNone(existing_row.sha256)
        self.assertIsNotNone(existing_row.updated_at)
        mock_session.commit.assert_called_once()

    @mock.patch("backend.app.services.chat_tools.db_service")
    def test_raises_when_dek_missing(self, mock_db_service) -> None:
        mock_db_service.is_stateless_db_mode.return_value = True
        mock_db_service.SessionLocal = mock.MagicMock()

        from backend.app.services.chat_tools import _save_qa_to_db
        qa = _make_qa_output()
        with self.assertRaises(ValueError) as ctx:
            _save_qa_to_db("testuser", qa, user_dek=None)
        self.assertIn("user_dek is required", str(ctx.exception))

    @mock.patch("backend.app.services.chat_tools.db_service")
    def test_skips_non_stateless_mode(self, mock_db_service) -> None:
        mock_db_service.is_stateless_db_mode.return_value = False

        from backend.app.services.chat_tools import _save_qa_to_db
        qa = _make_qa_output()
        _save_qa_to_db("testuser", qa, user_dek=None)

        mock_db_service.SessionLocal.assert_not_called()


class PathTraversalTests(unittest.TestCase):
    """Tests for path traversal protection in account_sync.py."""

    def test_relative_account_path_valid(self) -> None:
        from backend.app.services.account_sync import _relative_account_path
        from pathlib import Path
        result = _relative_account_path("testuser", Path("engine/accounts/testuser/output/test.json"))
        self.assertEqual(result, "output/test.json")

    def test_relative_account_path_traversal_raises(self) -> None:
        from backend.app.services.account_sync import _relative_account_path
        from pathlib import Path
        with self.assertRaises(RuntimeError):
            _relative_account_path("testuser", Path("/etc/passwd"))


class SyncReportQaTests(unittest.TestCase):
    """Tests for SyncReport QA tracking."""

    def test_sync_report_has_qa_fields(self) -> None:
        from backend.app.services.account_sync import SyncReport
        report = SyncReport(account_id="testuser")
        self.assertFalse(report.qa_artifact_synced)
        self.assertEqual(report.warnings, [])

    def test_check_qa_presence_warns_when_no_qa(self) -> None:
        from backend.app.services.account_sync import SyncReport, _check_qa_presence
        report = SyncReport(account_id="testuser")
        report.qa_artifact_synced = False

        with mock.patch("backend.app.services.account_sync._account_store") as mock_store_fn:
            mock_store = mock.Mock()
            mock_store.list_lessons.return_value = [{"lesson_id": "l1"}]
            mock_store_fn.return_value = mock_store
            _check_qa_presence("testuser", report)

        self.assertEqual(len(report.warnings), 1)
        self.assertIn("Lessons exist", report.warnings[0])

    def test_check_qa_presence_no_warning_when_synced(self) -> None:
        from backend.app.services.account_sync import SyncReport, _check_qa_presence
        report = SyncReport(account_id="testuser")
        report.qa_artifact_synced = True

        with mock.patch("backend.app.services.account_sync._account_store") as mock_store_fn:
            mock_store = mock.Mock()
            mock_store.list_lessons.return_value = [{"lesson_id": "l1"}]
            mock_store_fn.return_value = mock_store
            _check_qa_presence("testuser", report)

        self.assertEqual(len(report.warnings), 0)


class BuildTopicQuizTests(unittest.TestCase):
    """Tests for quiz_service.build_topic_quiz."""

    @mock.patch("backend.app.services.chat_tools._try_generate_qa", return_value=None)
    @mock.patch("backend.app.services.chat_tools._load_qa")
    @mock.patch("backend.app.services.quiz_service.build_quiz")
    def test_build_topic_quiz_success(self, mock_build, mock_load, mock_gen) -> None:
        mock_load.return_value = _make_qa_output()
        mock_build.return_value = mock.Mock(quiz_id="q1", questions=[mock.Mock()], build_indicator="ok")

        from backend.app.services.quiz_service import build_topic_quiz
        result = build_topic_quiz(account_id="testuser", topic="Binary Trees")

        mock_load.assert_called_once_with("testuser", user_dek=None)
        mock_build.assert_called_once()
        self.assertEqual(result.quiz_id, "q1")

    @mock.patch("backend.app.services.chat_tools._try_generate_qa", return_value=None)
    @mock.patch("backend.app.services.chat_tools._load_qa", return_value=None)
    @mock.patch("backend.app.services.quiz_service.build_quiz")
    def test_build_topic_quiz_no_qa_raises(self, mock_build, mock_load, mock_gen) -> None:
        from backend.app.services.quiz_service import build_topic_quiz
        with self.assertRaises(ValueError):
            build_topic_quiz(account_id="testuser", topic="Binary Trees")


class HandleLessonQuizTests(unittest.TestCase):
    """Tests for _handle_lesson_quiz embedded harness quizzes."""

    @mock.patch("backend.app.services.chat_tools._account_store")
    def test_lesson_quiz_without_embedded_quiz_requires_harness_regeneration(self, mock_store_fn) -> None:
        mock_store = mock.Mock()
        mock_store.get_active_lesson.return_value = {
            "topic": "trees",
            "checkpoints": [{"difficulty": "easy", "num_questions": 2, "pass_threshold_percent": 60.0, "question_mode": "mcq", "title": " Basics"}],
            "current_checkpoint_index": 0,
        }
        mock_store_fn.return_value = mock_store

        from backend.app.services.chat_tools import _handle_lesson_quiz
        result = _handle_lesson_quiz(account_id="testuser", user_dek=None)

        self.assertIn("no embedded quiz data", result)
        self.assertIn("backend lesson harness", result)

    @mock.patch("backend.app.services.chat_tools._account_store")
    def test_lesson_quiz_uses_embedded_quiz(self, mock_store_fn) -> None:
        mock_store = mock.Mock()
        mock_store.get_active_lesson.return_value = {
            "lesson_id": "lesson_trees",
            "topic": "trees",
            "checkpoints": [{
                "difficulty": "easy",
                "num_questions": 1,
                "pass_threshold_percent": 60.0,
                "question_mode": "mcq",
                "title": "Basics",
                "quiz": [
                    {
                        "question_id": "trees_1",
                        "topic_id": "trees",
                        "topic_title": "trees",
                        "question": "What is a binary tree?",
                        "expected_answer": "A tree with at most two children.",
                        "difficulty": "easy",
                        "question_mode": "mcq",
                        "options": ["A tree with at most two children", "A linked list", "A graph", "A hash table"],
                        "correct_option_index": 0,
                    },
                ],
            }],
            "current_checkpoint_index": 0,
        }
        mock_store_fn.return_value = mock_store

        from backend.app.services.chat_tools import _handle_lesson_quiz
        result = _handle_lesson_quiz(account_id="testuser", user_dek=None)

        self.assertIn("Basics", result)
        self.assertIn("trees_1", result)

    @mock.patch("backend.app.services.chat_tools._account_store")
    def test_harness_lesson_without_embedded_quiz_does_not_fallback(self, mock_store_fn) -> None:
        mock_store = mock.Mock()
        mock_store.get_active_lesson.return_value = {
            "lesson_id": "lesson_trees",
            "topic": "trees",
            "checkpoints": [{
                "difficulty": "easy",
                "num_questions": 1,
                "pass_threshold_percent": 60.0,
                "question_mode": "mcq",
                "title": "Basics",
                "markdown": "# Basics",
                "quiz": [],
                "sources": ["trees.md"],
            }],
            "current_checkpoint_index": 0,
        }
        mock_store_fn.return_value = mock_store

        from backend.app.services.chat_tools import _handle_lesson_quiz
        result = _handle_lesson_quiz(account_id="testuser", user_dek=None)

        self.assertIn("no embedded quiz data", result)


class HandleQuizTests(unittest.TestCase):
    """Tests for _handle_quiz auto-generate fallback."""

    @mock.patch("backend.app.services.chat_tools._account_store")
    @mock.patch("backend.app.services.chat_tools.build_topic_quiz")
    def test_handle_quiz_uses_quiz_service(self, mock_build, mock_store_fn) -> None:
        mock_store = mock.Mock()
        mock_store_fn.return_value = mock_store
        mock_question = mock.Mock(question="What is a tree?", difficulty="easy", options=None)
        mock_question.model_dump.return_value = {"question": "What is a tree?"}
        mock_quiz = mock.Mock(
            quiz_id="q1", questions=[mock_question],
            build_indicator="ok", warnings=[], failure_reason="none",
        )
        mock_quiz.model_dump.return_value = {"quiz_id": "q1", "questions": [{"question": "What is a tree?"}]}
        mock_build.return_value = mock_quiz

        from backend.app.services.chat_tools import _handle_quiz
        result = _handle_quiz(topic="trees", account_id="testuser", user_dek=None)

        mock_build.assert_called_once()
        self.assertIn("Quiz: trees", result)


class GradeLessonQuizTests(unittest.TestCase):
    """Tests for quiz_service.grade_lesson_quiz."""

    def test_build_checkpoint_quiz_legacy_path_disabled(self) -> None:
        from backend.app.services.quiz_service import build_checkpoint_quiz

        with self.assertRaises(RuntimeError):
            build_checkpoint_quiz("admin")

    @mock.patch("backend.app.services.quiz_service._account_store")
    def test_grade_lesson_quiz_mcq_correct(self, mock_store_fn) -> None:
        mock_store = mock.Mock()
        mock_store.get_active_lesson.return_value = {
            "lesson_id": "lesson_trees",
            "topic": "trees",
            "checkpoints": [{
                "title": "Basics",
                "difficulty": "easy",
                "num_questions": 2,
                "pass_threshold_percent": 60.0,
                "question_mode": "mcq",
                "quiz": [
                    {
                        "question_id": "trees_1",
                        "topic_id": "trees",
                        "topic_title": "trees",
                        "question": "What is a binary tree?",
                        "expected_answer": "A tree with at most two children.",
                        "difficulty": "easy",
                        "question_mode": "mcq",
                        "options": ["A tree with at most two children", "A linked list", "A graph", "A hash table"],
                        "correct_option_index": 0,
                    },
                ],
            }],
            "current_checkpoint_index": 0,
        }
        mock_store_fn.return_value = mock_store

        from backend.app.services.quiz_service import grade_lesson_quiz
        result = grade_lesson_quiz(
            account_id="testuser",
            lesson_id="lesson_trees",
            checkpoint_index=0,
            answers=[{"question_id": "trees_1", "answer": "A"}],
        )

        self.assertEqual(result.total_questions, 1)
        self.assertEqual(result.correct_answers, 1)
        self.assertEqual(result.score_percent, 100.0)

    @mock.patch("backend.app.services.quiz_service._account_store")
    def test_grade_lesson_quiz_mcq_incorrect(self, mock_store_fn) -> None:
        mock_store = mock.Mock()
        mock_store.get_active_lesson.return_value = {
            "lesson_id": "lesson_trees",
            "topic": "trees",
            "checkpoints": [{
                "title": "Basics",
                "difficulty": "easy",
                "num_questions": 1,
                "pass_threshold_percent": 60.0,
                "question_mode": "mcq",
                "quiz": [
                    {
                        "question_id": "trees_1",
                        "topic_id": "trees",
                        "topic_title": "trees",
                        "question": "What is a binary tree?",
                        "expected_answer": "A tree with at most two children.",
                        "difficulty": "easy",
                        "question_mode": "mcq",
                        "options": ["A tree with at most two children", "A linked list", "A graph", "A hash table"],
                        "correct_option_index": 0,
                    },
                ],
            }],
            "current_checkpoint_index": 0,
        }
        mock_store_fn.return_value = mock_store

        from backend.app.services.quiz_service import grade_lesson_quiz
        result = grade_lesson_quiz(
            account_id="testuser",
            lesson_id="lesson_trees",
            checkpoint_index=0,
            answers=[{"question_id": "trees_1", "answer": "B"}],
        )

        self.assertEqual(result.total_questions, 1)
        self.assertEqual(result.correct_answers, 0)
        self.assertEqual(result.score_percent, 0.0)

    @mock.patch("backend.app.services.quiz_service._account_store")
    def test_grade_lesson_quiz_no_active_lesson_raises(self, mock_store_fn) -> None:
        mock_store = mock.Mock()
        mock_store.get_active_lesson.return_value = None
        mock_store_fn.return_value = mock_store

        from backend.app.services.quiz_service import grade_lesson_quiz
        with self.assertRaises(ValueError):
            grade_lesson_quiz(
                account_id="testuser",
                lesson_id="lesson_trees",
                checkpoint_index=0,
                answers=[],
            )

    @mock.patch("backend.app.services.quiz_service._account_store")
    def test_grade_lesson_quiz_negative_checkpoint_index_raises(self, mock_store_fn) -> None:
        mock_store = mock.Mock()
        mock_store.get_active_lesson.return_value = {
            "lesson_id": "lesson_trees",
            "topic": "trees",
            "checkpoints": [{
                "title": "Basics",
                "difficulty": "easy",
                "quiz": [
                    {
                        "question_id": "trees_1",
                        "topic_id": "trees",
                        "topic_title": "trees",
                        "question": "What is a binary tree?",
                        "expected_answer": "A tree with at most two children.",
                        "difficulty": "easy",
                        "question_mode": "open",
                    },
                ],
            }],
            "current_checkpoint_index": 0,
        }
        mock_store_fn.return_value = mock_store

        from backend.app.services.quiz_service import grade_lesson_quiz
        with self.assertRaises(ValueError):
            grade_lesson_quiz(
                account_id="testuser",
                lesson_id="lesson_trees",
                checkpoint_index=-1,
                answers=[{"question_id": "trees_1", "answer": "A"}],
            )

    @mock.patch("backend.app.services.quiz_service._account_store")
    def test_grade_lesson_quiz_lesson_id_mismatch_raises(self, mock_store_fn) -> None:
        mock_store = mock.Mock()
        mock_store.get_active_lesson.return_value = {
            "lesson_id": "lesson_other",
            "topic": "other",
            "checkpoints": [],
            "current_checkpoint_index": 0,
        }
        mock_store_fn.return_value = mock_store

        from backend.app.services.quiz_service import grade_lesson_quiz
        with self.assertRaises(ValueError):
            grade_lesson_quiz(
                account_id="testuser",
                lesson_id="lesson_trees",
                checkpoint_index=0,
                answers=[],
            )

    @mock.patch("backend.app.services.quiz_service._account_store")
    def test_grade_lesson_quiz_no_quiz_data_raises(self, mock_store_fn) -> None:
        mock_store = mock.Mock()
        mock_store.get_active_lesson.return_value = {
            "lesson_id": "lesson_trees",
            "topic": "trees",
            "checkpoints": [{
                "title": "Basics",
                "difficulty": "easy",
                "num_questions": 1,
                "pass_threshold_percent": 60.0,
                "question_mode": "mcq",
            }],
            "current_checkpoint_index": 0,
        }
        mock_store_fn.return_value = mock_store

        from backend.app.services.quiz_service import grade_lesson_quiz
        with self.assertRaises(ValueError):
            grade_lesson_quiz(
                account_id="testuser",
                lesson_id="lesson_trees",
                checkpoint_index=0,
                answers=[],
            )

    @mock.patch("backend.app.services.quiz_service._account_store")
    def test_grade_lesson_quiz_corrupt_questions_raise_clear_error(self, mock_store_fn) -> None:
        mock_store = mock.Mock()
        mock_store.get_active_lesson.return_value = {
            "lesson_id": "lesson_trees",
            "topic": "trees",
            "checkpoints": [{
                "title": "Basics",
                "difficulty": "easy",
                "num_questions": 1,
                "pass_threshold_percent": 60.0,
                "question_mode": "mcq",
                "quiz": [
                    {
                        "question_id": "trees_1",
                        "topic_id": "trees",
                        "topic_title": "trees",
                        "question": "What is a binary tree?",
                        "expected_answer": "A tree with at most two children.",
                        "difficulty": "easy",
                        "question_mode": "not-a-mode",
                    },
                    {"question_id": "empty", "question": ""},
                ],
            }],
            "current_checkpoint_index": 0,
        }
        mock_store_fn.return_value = mock_store

        from backend.app.services.quiz_service import grade_lesson_quiz
        with self.assertRaisesRegex(ValueError, "no valid quiz questions"):
            grade_lesson_quiz(
                account_id="testuser",
                lesson_id="lesson_trees",
                checkpoint_index=0,
                answers=[],
            )


if __name__ == "__main__":
    unittest.main()
