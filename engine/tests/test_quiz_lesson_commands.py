from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.quiz import account_system
from engine.quiz.account_system import AccountStore
from engine.quiz.quiz_chatbot_cli import (
    _extract_lesson_selector,
    _is_open_lesson_request,
    _is_restart_lesson_request,
    _is_resume_lesson_request,
    _resolve_lesson_selector,
)


class QuizLessonCommandTests(unittest.TestCase):
    def test_resume_open_restart_detection(self) -> None:
        self.assertTrue(_is_resume_lesson_request("resume 1"))
        self.assertTrue(_is_resume_lesson_request("resume lesson 2"))
        self.assertTrue(_is_open_lesson_request("open lesson lesson_trees"))
        self.assertTrue(_is_restart_lesson_request("restart lesson 3"))

    def test_extract_lesson_selector(self) -> None:
        self.assertEqual(_extract_lesson_selector("resume 1"), "1")
        self.assertEqual(_extract_lesson_selector("resume lesson 2"), "2")
        self.assertEqual(_extract_lesson_selector("open lesson lesson_graphs"), "lesson_graphs")
        self.assertEqual(_extract_lesson_selector("restart lesson lesson_dp"), "lesson_dp")
        self.assertIsNone(_extract_lesson_selector("resume"))

    def test_resolve_lesson_selector_by_index_and_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                store.create_user(username="alice", pin="1234")
                checkpoints = [{"title": "c1", "difficulty": "easy", "num_questions": 2, "pass_threshold_percent": 60.0}]
                store.start_lesson(
                    username="alice",
                    lesson_id="lesson_graphs",
                    title="Lesson: graphs",
                    topic="graphs",
                    checkpoints=checkpoints,
                )
                store.start_lesson(
                    username="alice",
                    lesson_id="lesson_trees",
                    title="Lesson: trees",
                    topic="trees",
                    checkpoints=checkpoints,
                )
                lessons = store.list_lessons(username="alice")
                self.assertEqual(len(lessons), 2)

                # Index resolution should map to current displayed order.
                resolved_first = _resolve_lesson_selector(account_store=store, username="alice", selector="1")
                self.assertEqual(resolved_first, str(lessons[0]["lesson_id"]))

                # Exact id resolution.
                resolved_id = _resolve_lesson_selector(
                    account_store=store, username="alice", selector="lesson_graphs"
                )
                self.assertEqual(resolved_id, "lesson_graphs")

                # Unknown selector.
                self.assertIsNone(_resolve_lesson_selector(account_store=store, username="alice", selector="999"))
                self.assertIsNone(
                    _resolve_lesson_selector(account_store=store, username="alice", selector="lesson_missing")
                )


if __name__ == "__main__":
    unittest.main()
