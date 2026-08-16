from __future__ import annotations

import unittest

from engine.quiz.quiz_chatbot_cli import (
    _default_lesson_checkpoints,
    _normalize_lesson_checkpoint,
)


class NormalizeLessonCheckpointTests(unittest.TestCase):
    def test_valid_checkpoint_with_mcq_mode(self) -> None:
        result = _normalize_lesson_checkpoint({
            "title": "trees fundamentals",
            "difficulty": "easy",
            "num_questions": 2,
            "pass_threshold_percent": 60.0,
            "question_mode": "mcq",
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["question_mode"], "mcq")
        self.assertEqual(result["difficulty"], "easy")
        self.assertEqual(result["num_questions"], 2)
        self.assertEqual(result["pass_threshold_percent"], 60.0)

    def test_valid_checkpoint_with_open_mode(self) -> None:
        result = _normalize_lesson_checkpoint({
            "title": "trees application",
            "difficulty": "medium",
            "num_questions": 3,
            "pass_threshold_percent": 70.0,
            "question_mode": "open",
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["question_mode"], "open")

    def test_missing_question_mode_defaults_to_mcq(self) -> None:
        result = _normalize_lesson_checkpoint({
            "title": "trees mastery",
            "difficulty": "hard",
            "num_questions": 3,
            "pass_threshold_percent": 75.0,
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["question_mode"], "mcq")

    def test_invalid_question_mode_defaults_to_mcq(self) -> None:
        result = _normalize_lesson_checkpoint({
            "title": "test",
            "difficulty": "medium",
            "num_questions": 2,
            "pass_threshold_percent": 70.0,
            "question_mode": "invalid_mode",
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["question_mode"], "mcq")

    def test_empty_string_question_mode_defaults_to_mcq(self) -> None:
        result = _normalize_lesson_checkpoint({
            "title": "test",
            "difficulty": "medium",
            "num_questions": 2,
            "pass_threshold_percent": 70.0,
            "question_mode": "",
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["question_mode"], "mcq")

    def test_question_mode_case_insensitive(self) -> None:
        result = _normalize_lesson_checkpoint({
            "title": "test",
            "difficulty": "medium",
            "num_questions": 2,
            "pass_threshold_percent": 70.0,
            "question_mode": "MCQ",
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["question_mode"], "mcq")

    def test_non_dict_input_returns_none(self) -> None:
        self.assertIsNone(_normalize_lesson_checkpoint("not a dict"))
        self.assertIsNone(_normalize_lesson_checkpoint(123))
        self.assertIsNone(_normalize_lesson_checkpoint(None))
        self.assertIsNone(_normalize_lesson_checkpoint([]))

    def test_invalid_difficulty_defaults_to_medium(self) -> None:
        result = _normalize_lesson_checkpoint({
            "title": "test",
            "difficulty": "invalid",
            "num_questions": 2,
            "pass_threshold_percent": 70.0,
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["difficulty"], "medium")

    def test_num_questions_out_of_range_returns_none(self) -> None:
        self.assertIsNone(_normalize_lesson_checkpoint({
            "title": "test",
            "difficulty": "medium",
            "num_questions": 0,
            "pass_threshold_percent": 70.0,
        }))
        self.assertIsNone(_normalize_lesson_checkpoint({
            "title": "test",
            "difficulty": "medium",
            "num_questions": 51,
            "pass_threshold_percent": 70.0,
        }))

    def test_invalid_num_questions_returns_none(self) -> None:
        self.assertIsNone(_normalize_lesson_checkpoint({
            "title": "test",
            "difficulty": "medium",
            "num_questions": "not a number",
            "pass_threshold_percent": 70.0,
        }))

    def test_threshold_out_of_range_returns_none(self) -> None:
        self.assertIsNone(_normalize_lesson_checkpoint({
            "title": "test",
            "difficulty": "medium",
            "num_questions": 2,
            "pass_threshold_percent": -1.0,
        }))
        self.assertIsNone(_normalize_lesson_checkpoint({
            "title": "test",
            "difficulty": "medium",
            "num_questions": 2,
            "pass_threshold_percent": 101.0,
        }))

    def test_empty_title_defaults_to_checkpoint(self) -> None:
        result = _normalize_lesson_checkpoint({
            "title": "",
            "difficulty": "medium",
            "num_questions": 2,
            "pass_threshold_percent": 70.0,
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "checkpoint")

    def test_boundary_values_easy(self) -> None:
        result = _normalize_lesson_checkpoint({
            "title": "test",
            "difficulty": "easy",
            "num_questions": 1,
            "pass_threshold_percent": 0.0,
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["difficulty"], "easy")
        self.assertEqual(result["num_questions"], 1)
        self.assertEqual(result["pass_threshold_percent"], 0.0)

    def test_boundary_values_hard(self) -> None:
        result = _normalize_lesson_checkpoint({
            "title": "test",
            "difficulty": "hard",
            "num_questions": 50,
            "pass_threshold_percent": 100.0,
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["difficulty"], "hard")
        self.assertEqual(result["num_questions"], 50)
        self.assertEqual(result["pass_threshold_percent"], 100.0)


class DefaultLessonCheckpointsTests(unittest.TestCase):
    def test_default_checkpoints_include_question_mode(self) -> None:
        checkpoints = _default_lesson_checkpoints("trees")
        self.assertEqual(len(checkpoints), 3)
        for cp in checkpoints:
            self.assertIn("question_mode", cp)
            self.assertEqual(cp["question_mode"], "mcq")

    def test_default_checkpoints_structure(self) -> None:
        checkpoints = _default_lesson_checkpoints("dijkstra")
        self.assertEqual(checkpoints[0]["title"], "dijkstra fundamentals")
        self.assertEqual(checkpoints[0]["difficulty"], "easy")
        self.assertEqual(checkpoints[0]["num_questions"], 2)
        self.assertEqual(checkpoints[0]["pass_threshold_percent"], 60.0)

        self.assertEqual(checkpoints[1]["title"], "dijkstra application")
        self.assertEqual(checkpoints[1]["difficulty"], "medium")
        self.assertEqual(checkpoints[1]["num_questions"], 3)
        self.assertEqual(checkpoints[1]["pass_threshold_percent"], 70.0)

        self.assertEqual(checkpoints[2]["title"], "dijkstra mastery")
        self.assertEqual(checkpoints[2]["difficulty"], "hard")
        self.assertEqual(checkpoints[2]["num_questions"], 3)
        self.assertEqual(checkpoints[2]["pass_threshold_percent"], 75.0)

    def test_default_checkpoints_with_special_characters(self) -> None:
        checkpoints = _default_lesson_checkpoints("A* & Dijkstra")
        self.assertEqual(checkpoints[0]["title"], "A* & Dijkstra fundamentals")


if __name__ == "__main__":
    unittest.main()
