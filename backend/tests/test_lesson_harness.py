from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.app.schemas import Doc, DocChunk
from backend.app.services import chat_tools
from backend.app.services import lesson_harness
from backend.app.services import quiz_harness
from engine.quiz.quiz_backend import QuizQuestion


class LessonHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        lesson_harness._jobs = None

    def tearDown(self) -> None:
        lesson_harness._jobs = None

    @staticmethod
    def _good_lesson_model(**kwargs):
        role = kwargs["role"]
        fallback = kwargs["fallback"]
        if role == "curriculum_planner":
            return fallback
        if role == "context_refiner":
            return fallback
        if role == "lesson_reviewer":
            return {"approved": True, "issues": [], "rejection_reason": ""}
        if role.endswith("_lesson_quiz_worker"):
            payload = kwargs["payload"]
            part = payload["part"]
            topic = payload["topic"]
            return {
                "title": part["title"],
                "difficulty": part["difficulty"],
                "markdown": (
                    f"# {part['title']}\n\n"
                    f"## Goal\n{part['goal']}\n\n"
                    f"## Focus\n{part.get('focus', 'specific focus')}\n\n"
                    f"## Explanation\n{topic} is explained with enough detail for a real checkpoint. "
                    "This section defines the topic, states the problem it solves, walks through the core mechanism, "
                    "and connects each decision to the learner's goal. The explanation is intentionally long enough "
                    "to avoid placeholder output and to give the student a usable study note. It also names the "
                    "inputs, describes the expected output, and explains why the procedure is correct at a beginner "
                    "level. The student should be able to read this checkpoint without seeing phrases like create "
                    "your own example or describe it yourself as the main teaching content.\n\n"
                    "## Worked Example\nStart from a small input, apply the first rule, record the intermediate state, "
                    "then repeat until the result is reached. The important part is showing the state after each step "
                    "so the student can see why the algorithm or concept works. After the final step, compare the "
                    "result with the original goal and state what changed from the starting state.\n\n"
                    "## Common Mistakes\n- Skipping definitions.\n- Jumping straight to the answer.\n"
                    "- Forgetting the assumptions that make the method valid.\n\n"
                    "## Practice\nCreate a similar example and explain each step in your own words."
                ),
                "quiz": [
                    {
                        "question": f"What is the goal of {part['title']}?",
                        "options": ["Skip examples", part["goal"], "Ignore assumptions", "Memorize only"],
                        "correct_option_index": 1,
                        "expected_answer": part["goal"],
                        "explanation": "The goal states what this checkpoint teaches.",
                    }
                ],
                "sources": ["test source"],
            }
        return fallback

    def test_rejects_too_broad_topic(self) -> None:
        self.assertIn("too broad", lesson_harness._preflight_rejection("biology"))

    def test_create_lesson_job_saves_three_markdown_parts(self) -> None:
        docs = [
            Doc(
                id="d1",
                name="trees.md",
                uploaded_at=lesson_harness.datetime.now(lesson_harness.UTC),
                chunks=[
                    DocChunk(
                        chunk_id="c1",
                        text="AVL trees and red black trees are self balancing binary search trees.",
                    )
                ],
            )
        ]
        fake_store = mock.Mock()
        fake_store.start_lesson.return_value = {"current_checkpoint_index": 0}

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(lesson_harness, "_JOBS_PATH", Path(td) / "jobs.json"):
                lesson_harness._jobs = None
                with mock.patch("backend.app.services.lesson_harness.list_docs", return_value=docs):
                    with mock.patch("backend.app.services.lesson_harness._call_json_model", side_effect=self._good_lesson_model):
                        with mock.patch("backend.app.services.lesson_harness._call_json_model_with_tools", side_effect=self._good_lesson_model):
                            with mock.patch("backend.app.services.lesson_harness.AccountStore", return_value=fake_store):
                                with mock.patch("threading.Thread.start", lambda self: self._target(**self._kwargs)):
                                    job = lesson_harness.create_lesson_generation_job(
                                        account_id="admin",
                                        request=lesson_harness.LessonGenerateRequest(topic="AVL trees"),
                                    )
                                    saved = lesson_harness.get_lesson_generation_job(job_id=job["job_id"], account_id="admin")

        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(saved["result"]["topic"], "AVL trees")
        subtasks = {item["name"]: item for item in saved["subtasks"]}
        self.assertEqual(subtasks["planner"]["status"], "succeeded")
        self.assertEqual(subtasks["foundations_worker"]["status"], "succeeded")
        self.assertEqual(subtasks["application_worker"]["status"], "succeeded")
        self.assertEqual(subtasks["mastery_worker"]["status"], "succeeded")
        self.assertEqual(subtasks["reviewer"]["status"], "succeeded")
        self.assertEqual(subtasks["saver"]["status"], "succeeded")
        checkpoints = fake_store.start_lesson.call_args.kwargs["checkpoints"]
        self.assertEqual(len(checkpoints), 3)
        self.assertTrue(all(cp["markdown"].startswith("#") for cp in checkpoints))
        self.assertTrue(all(cp["quiz"] for cp in checkpoints))
        self.assertEqual(saved["result"]["quality"]["context_source"], "local")
        self.assertFalse(saved["result"]["quality"]["fallback_used"])
        self.assertTrue(all(cp["context_source"] == "local" for cp in checkpoints))

    def test_cancel_lesson_job_marks_active_subtasks_canceled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(lesson_harness, "_JOBS_PATH", Path(td) / "jobs.json"):
                lesson_harness._jobs = None
                with mock.patch("threading.Thread.start", lambda self: None):
                    job = lesson_harness.create_lesson_generation_job(
                        account_id="admin",
                        request=lesson_harness.LessonGenerateRequest(topic="AVL trees"),
                    )
                lesson_harness._set_subtask(
                    job["job_id"],
                    "foundations_worker",
                    status="running",
                    phase="calling_model",
                    progress=35,
                )
                canceled = lesson_harness.cancel_lesson_generation_job(
                    job_id=job["job_id"],
                    account_id="admin",
                )

        self.assertIsNotNone(canceled)
        self.assertEqual(canceled["status"], "canceled")
        self.assertIsNone(canceled["result"])
        subtasks = {item["name"]: item for item in canceled["subtasks"]}
        self.assertEqual(subtasks["planner"]["status"], "canceled")
        self.assertEqual(subtasks["foundations_worker"]["status"], "canceled")

    def test_create_lesson_job_uses_short_primer_when_no_context(self) -> None:
        fake_store = mock.Mock()
        fake_store.start_lesson.return_value = {"current_checkpoint_index": 0}

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(lesson_harness, "_JOBS_PATH", Path(td) / "jobs.json"):
                lesson_harness._jobs = None
                with mock.patch("backend.app.services.lesson_harness.list_docs", return_value=[]):
                    with mock.patch("backend.app.services.lesson_harness._collect_wikipedia_context", return_value=[]):
                        with mock.patch("backend.app.services.lesson_harness._call_json_model", side_effect=self._good_lesson_model):
                            with mock.patch("backend.app.services.lesson_harness._call_json_model_with_tools", side_effect=self._good_lesson_model):
                                with mock.patch("backend.app.services.lesson_harness.AccountStore", return_value=fake_store):
                                    with mock.patch("threading.Thread.start", lambda self: self._target(**self._kwargs)):
                                        job = lesson_harness.create_lesson_generation_job(
                                            account_id="admin",
                                            request=lesson_harness.LessonGenerateRequest(topic="AVL trees"),
                                        )
                                        saved = lesson_harness.get_lesson_generation_job(job_id=job["job_id"], account_id="admin")

        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(saved["result"]["topic"], "AVL trees")
        checkpoints = fake_store.start_lesson.call_args.kwargs["checkpoints"]
        self.assertEqual(len(checkpoints), 3)
        self.assertTrue(all(cp["quiz"] for cp in checkpoints))

    def test_create_lesson_job_rejects_fallback_only_worker_output(self) -> None:
        fake_store = mock.Mock()

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(lesson_harness, "_JOBS_PATH", Path(td) / "jobs.json"):
                lesson_harness._jobs = None
                with mock.patch("backend.app.services.lesson_harness.list_docs", return_value=[]):
                    with mock.patch("backend.app.services.lesson_harness._collect_wikipedia_context", return_value=[]):
                        with mock.patch("backend.app.services.lesson_harness._call_json_model", side_effect=lambda **kwargs: kwargs["fallback"]):
                            with mock.patch("backend.app.services.lesson_harness._call_json_model_with_tools", side_effect=lambda **kwargs: kwargs["fallback"]):
                                with mock.patch("backend.app.services.lesson_harness.AccountStore", return_value=fake_store):
                                    with mock.patch("threading.Thread.start", lambda self: self._target(**self._kwargs)):
                                        job = lesson_harness.create_lesson_generation_job(
                                            account_id="admin",
                                            request=lesson_harness.LessonGenerateRequest(topic="AVL trees"),
                                        )
                                        saved = lesson_harness.get_lesson_generation_job(job_id=job["job_id"], account_id="admin")

        self.assertEqual(saved["status"], "rejected")
        self.assertIn("No fallback lesson was saved", saved["rejection_reason"])
        fake_store.start_lesson.assert_not_called()

    def test_chat_tools_create_lesson_payload_uses_harness_and_saves_generated_lesson(self) -> None:
        fake_store = mock.Mock()
        fake_store.start_lesson.return_value = {"current_checkpoint_index": 0}

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(lesson_harness, "_JOBS_PATH", Path(td) / "jobs.json"):
                lesson_harness._jobs = None
                with mock.patch("backend.app.services.lesson_harness.list_docs", return_value=[]):
                    with mock.patch("backend.app.services.lesson_harness._collect_wikipedia_context", return_value=[]):
                        with mock.patch("backend.app.services.lesson_harness._call_json_model", side_effect=self._good_lesson_model):
                            with mock.patch("backend.app.services.lesson_harness._call_json_model_with_tools", side_effect=self._good_lesson_model):
                                with mock.patch("backend.app.services.lesson_harness.AccountStore", return_value=fake_store):
                                    with mock.patch("threading.Thread.start", lambda self: self._target(**self._kwargs)):
                                        payload = chat_tools.create_lesson_payload(
                                            topic="AVL trees",
                                            account_id="admin",
                                            use_harness=True,
                                        )
                                        saved = lesson_harness.get_lesson_generation_job(
                                            job_id=payload["harness_job_id"],
                                            account_id="admin",
                                        )

        self.assertEqual(payload["harness_status"], "queued")
        self.assertEqual(saved["status"], "succeeded")
        fake_store.start_lesson.assert_called_once()
        checkpoints = fake_store.start_lesson.call_args.kwargs["checkpoints"]
        self.assertEqual(len(checkpoints), 3)
        self.assertTrue(all(len(cp["markdown"]) >= quiz_harness._MIN_MARKDOWN_CHARS for cp in checkpoints))

    def test_collect_context_excludes_unrelated_chunks(self) -> None:
        docs = [
            Doc(
                id="d1",
                name="unrelated.md",
                uploaded_at=lesson_harness.datetime.now(lesson_harness.UTC),
                chunks=[
                    DocChunk(chunk_id="c1", text="Photosynthesis converts sunlight into chemical energy in plants."),
                    DocChunk(chunk_id="c2", text="AVL trees use rotations to maintain balance in binary search trees."),
                ],
            )
        ]
        with mock.patch("backend.app.services.lesson_harness.list_docs", return_value=docs):
            result = lesson_harness._collect_context("AVL trees", "admin", None, max_chunks=10)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["chunk_id"], "c2")

    def test_topic_overlaps_context_requires_minimum_ratio(self) -> None:
        contexts = [{"doc": "d", "chunk_id": "c", "text": "avl trees are balanced"}]
        self.assertTrue(lesson_harness._topic_overlaps_context("AVL trees", contexts))

        contexts = [{"doc": "d", "chunk_id": "c", "text": "completely unrelated content about cooking"}]
        self.assertFalse(lesson_harness._topic_overlaps_context("AVL trees", contexts))

    def test_collect_wikipedia_filters_unrelated_results(self) -> None:
        doc_tokens = {"avl", "trees", "binary", "search", "rotation"}
        wiki_snippets = [
            "AVL trees: A self-balancing binary search tree invented by Adelson-Velsky and Landis.",
            "Cooking: A guide to making pasta with tomato sauce and fresh basil.",
            "Red-black tree: Another self-balancing binary search tree structure.",
        ]
        with mock.patch("backend.app.services.chat_engine._search_wikipedia", return_value=wiki_snippets):
            result = lesson_harness._collect_wikipedia_context("AVL trees", doc_tokens=doc_tokens)

        self.assertEqual(len(result), 2)
        self.assertIn("AVL trees", result[0]["text"])
        self.assertIn("Red-black tree", result[1]["text"])

    def test_refine_context_keeps_relevant_chunks(self) -> None:
        contexts = [
            {"doc": "cs.md", "chunk_id": "c1", "text": "AVL trees use rotations to maintain balance."},
            {"doc": "biology.md", "chunk_id": "c2", "text": "Photosynthesis converts sunlight to chemical energy."},
            {"doc": "cs2.md", "chunk_id": "c3", "text": "Red-black trees are another balanced binary search tree."},
        ]
        fake_result = {"keep": [0, 2]}
        with mock.patch("backend.app.services.lesson_harness._call_json_model", return_value=fake_result):
            result = asyncio.run(lesson_harness._refine_context(
                job_id="test_job",
                topic="AVL trees",
                contexts=contexts,
                context_source="local",
            ))

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["chunk_id"], "c1")
        self.assertEqual(result[1]["chunk_id"], "c3")

    def test_refine_context_returns_original_on_empty_keep(self) -> None:
        contexts = [
            {"doc": "d1", "chunk_id": "c1", "text": "content A"},
            {"doc": "d2", "chunk_id": "c2", "text": "content B"},
        ]
        with mock.patch("backend.app.services.lesson_harness._call_json_model", return_value={"keep": []}):
            result = asyncio.run(lesson_harness._refine_context(
                job_id="test_job",
                topic="test",
                contexts=contexts,
                context_source="local",
            ))

        self.assertEqual(len(result), 2)

    def test_refine_context_skips_single_chunk(self) -> None:
        contexts = [{"doc": "d1", "chunk_id": "c1", "text": "only chunk"}]
        result = asyncio.run(lesson_harness._refine_context(
            job_id="test_job",
            topic="test",
            contexts=contexts,
            context_source="local",
        ))
        self.assertEqual(len(result), 1)

    def test_refine_context_returns_original_on_error(self) -> None:
        contexts = [
            {"doc": "d1", "chunk_id": "c1", "text": "content A"},
        ]
        with mock.patch("backend.app.services.lesson_harness._call_json_model", side_effect=RuntimeError("api down")):
            result = asyncio.run(lesson_harness._refine_context(
                job_id="test_job",
                topic="test",
                contexts=contexts,
                context_source="local",
            ))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["chunk_id"], "c1")

    def test_normalize_quiz_downgrades_invalid_mcq_index(self) -> None:
        quiz = quiz_harness.normalize_quiz(
            [
                {
                    "question": "Which option is correct?",
                    "options": ["Alpha", "Beta", "Gamma", "Delta"],
                    "correct_option_index": 8,
                    "expected_answer": "Explain the concept.",
                }
            ],
            "AVL trees",
            {"title": "AVL basics", "difficulty": "easy", "goal": "Explain rotations."},
        )

        self.assertEqual(quiz[0]["question_mode"], "open")
        self.assertEqual(quiz[0]["options"], [])
        self.assertIsNone(quiz[0]["correct_option_index"])

    def test_normalize_quiz_infers_mcq_index_from_expected_answer(self) -> None:
        quiz = quiz_harness.normalize_quiz(
            [
                {
                    "question": "Which option is correct?",
                    "options": ["Alpha", "Beta", "Gamma", "Delta"],
                    "correct_option_index": 8,
                    "expected_answer": "Gamma",
                }
            ],
            "AVL trees",
            {"title": "AVL basics", "difficulty": "easy", "goal": "Explain rotations."},
        )

        self.assertEqual(quiz[0]["question_mode"], "mcq")
        self.assertEqual(quiz[0]["correct_option_index"], 2)

    def test_normalize_quiz_fallback_is_quiz_question_compatible(self) -> None:
        quiz = quiz_harness.normalize_quiz(
            [{"question": ""}],
            "AVL trees",
            {"title": "AVL basics", "difficulty": "easy", "goal": "Explain rotations."},
        )

        self.assertEqual(quiz[0]["question_id"], "avl_trees_1")
        self.assertEqual(quiz[0]["question_mode"], "open")
        self.assertIn("topic_id", quiz[0])
        QuizQuestion(**quiz[0])

    def test_normalize_plan_accepts_one_two_four_and_five_parts(self) -> None:
        for count in (1, 2, 4, 5):
            raw = {
                "parts": [
                    {"title": f"Part {idx}", "difficulty": "medium", "goal": f"Goal {idx}"}
                    for idx in range(1, count + 1)
                ]
            }
            parts = lesson_harness._normalize_plan("AVL trees", None, raw)
            self.assertEqual(len(parts), count)
            self.assertTrue(all("focus" in part for part in parts))
            self.assertTrue(all("example_type" in part for part in parts))

    def test_normalize_plan_malformed_falls_back_to_three_parts(self) -> None:
        parts = lesson_harness._normalize_plan(
            "AVL trees",
            None,
            {"parts": []},
        )
        self.assertEqual(len(parts), 3)
        self.assertIn("foundations", parts[0]["title"])
        self.assertIn("focus", parts[0])

    def test_fallback_plan_has_distinct_part_briefs(self) -> None:
        parts = lesson_harness._fallback_plan("Dijkstra", None)["parts"]
        focuses = {part["focus"] for part in parts}
        examples = {part["example_type"] for part in parts}

        self.assertEqual(len(parts), 3)
        self.assertEqual(len(focuses), 3)
        self.assertEqual(len(examples), 3)

    def test_worker_payload_receives_all_part_briefs(self) -> None:
        captured = []

        async def fake_model(**kwargs):
            if kwargs["role"].endswith("_lesson_quiz_worker"):
                captured.append(kwargs["payload"])
            return self._good_lesson_model(**kwargs)

        fake_store = mock.Mock()
        fake_store.start_lesson.return_value = {"current_checkpoint_index": 0}

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(lesson_harness, "_JOBS_PATH", Path(td) / "jobs.json"):
                lesson_harness._jobs = None
                with mock.patch("backend.app.services.lesson_harness.list_docs", return_value=[]):
                    with mock.patch("backend.app.services.lesson_harness._collect_wikipedia_context", return_value=[]):
                        with mock.patch("backend.app.services.lesson_harness._call_json_model", side_effect=fake_model):
                            with mock.patch("backend.app.services.lesson_harness._call_json_model_with_tools", side_effect=fake_model):
                                with mock.patch("backend.app.services.lesson_harness.AccountStore", return_value=fake_store):
                                    with mock.patch("threading.Thread.start", lambda self: self._target(**self._kwargs)):
                                        lesson_harness.create_lesson_generation_job(
                                            account_id="admin",
                                            request=lesson_harness.LessonGenerateRequest(topic="Dijkstra"),
                                        )

        self.assertEqual(len(captured), 3)
        self.assertTrue(all(len(payload["all_parts"]) == 3 for payload in captured))
        self.assertEqual(len({payload["part"]["focus"] for payload in captured}), 3)

    def test_review_regenerates_only_quiz_when_markdown_is_usable(self) -> None:
        calls = []

        async def fake_model(**kwargs):
            role = kwargs["role"]
            calls.append(role)
            if role == "curriculum_planner":
                return kwargs["fallback"]
            if role == "context_refiner":
                return kwargs["fallback"]
            if role == "lesson_reviewer":
                return {"approved": True, "issues": [], "rejection_reason": ""}
            if role.endswith("_lesson_quiz_worker"):
                payload = kwargs["payload"]
                part = payload["part"]
                return {
                    "title": part["title"],
                    "difficulty": part["difficulty"],
                    "markdown": (
                        f"# {part['title']}\n\n"
                        f"## Goal\n{part['goal']}\n\n"
                        "AVL trees use rotations to restore balance after insertions. " * 25
                    ),
                    "quiz": [
                        {
                            "question": "What color is the tree?",
                            "expected_answer": "The tree is purple.",
                            "source_context": "",
                        }
                    ],
                    "sources": ["trees.md"],
                }
            if role.endswith("_quiz_retry_worker"):
                return {
                    "quiz": [
                        {
                            "question": "Why do AVL trees use rotations?",
                            "expected_answer": "Rotations restore balance.",
                            "explanation": "The checkpoint states rotations restore balance.",
                            "source_context": "trees.md#c1: AVL trees use rotations to restore balance.",
                        }
                    ]
                }
            return kwargs["fallback"]

        docs = [
            Doc(
                id="d1",
                name="trees.md",
                uploaded_at=lesson_harness.datetime.now(lesson_harness.UTC),
                chunks=[
                    DocChunk(
                        chunk_id="c1",
                        text="AVL trees use rotations to restore balance after insertions.",
                    )
                ],
            )
        ]
        fake_store = mock.Mock()
        fake_store.start_lesson.return_value = {"current_checkpoint_index": 0}

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(lesson_harness, "_JOBS_PATH", Path(td) / "jobs.json"):
                lesson_harness._jobs = None
                with mock.patch("backend.app.services.lesson_harness.list_docs", return_value=docs):
                    with mock.patch("backend.app.services.lesson_harness._call_json_model", side_effect=fake_model):
                        with mock.patch("backend.app.services.lesson_harness._call_json_model_with_tools", side_effect=fake_model):
                            with mock.patch("backend.app.services.lesson_harness.AccountStore", return_value=fake_store):
                                with mock.patch("threading.Thread.start", lambda self: self._target(**self._kwargs)):
                                    job = lesson_harness.create_lesson_generation_job(
                                        account_id="admin",
                                        request=lesson_harness.LessonGenerateRequest(topic="AVL trees"),
                                    )
                                    saved = lesson_harness.get_lesson_generation_job(job_id=job["job_id"], account_id="admin")

        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(sum(role.endswith("_lesson_quiz_worker") for role in calls), 3)
        self.assertEqual(sum(role.endswith("_quiz_retry_worker") for role in calls), 3)
        checkpoints = fake_store.start_lesson.call_args.kwargs["checkpoints"]
        self.assertTrue(all(cp["quiz"][0]["expected_answer"] == "Rotations restore balance." for cp in checkpoints))

    def test_normalize_quiz_populates_source_context(self) -> None:
        quiz = quiz_harness.normalize_quiz(
            [
                {
                    "question": "Why do AVL trees use rotations?",
                    "expected_answer": "To maintain balance.",
                }
            ],
            "AVL trees",
            {"title": "AVL basics", "difficulty": "easy", "goal": "Explain rotations."},
            contexts=[{"doc": "trees.md", "chunk_id": "c1", "text": "AVL trees use rotations to maintain balance."}],
        )

        self.assertIn("trees.md#c1", quiz[0]["source_context"])
        QuizQuestion(**quiz[0])

    def test_normalize_part_does_not_mark_missing_sources_as_fallback(self) -> None:
        part = lesson_harness._normalize_part(
            "AVL trees",
            {"title": "AVL basics", "difficulty": "easy", "goal": "Explain rotations."},
            {
                "markdown": "x" * 950,
                "quiz": [{"question": "Why rotate?", "expected_answer": "To restore balance."}],
            },
            contexts=[{"doc": "wiki", "chunk_id": "c1", "text": "AVL trees use rotations to restore balance."}],
            index=1,
        )

        self.assertFalse(part["fallback_used"])
        self.assertEqual(part["sources"], ["wiki"])

    def test_normalize_part_accepts_common_model_key_variants(self) -> None:
        part = lesson_harness._normalize_part(
            "AVL trees",
            {"title": "AVL basics", "difficulty": "easy", "goal": "Explain rotations."},
            {
                "content": "y" * 950,
                "questions": [{"question": "What is the goal?", "expected_answer": "Explain rotations."}],
                "source": "model source",
            },
            contexts=[],
            index=1,
        )

        self.assertFalse(part["fallback_used"])
        self.assertEqual(part["sources"], ["model source"])

    def test_quality_issues_detect_fallback_and_empty_quiz(self) -> None:
        weak = quiz_harness.find_part_quality_issues([
            {"markdown": "# Short", "quiz": [], "fallback_used": True},
            {
                "markdown": "Balance is a height constraint." * 20,
                "quiz": [{"question": "What is balance?", "expected_answer": "A height constraint."}],
            },
        ])

        self.assertEqual(weak, [0, 1])

    def test_part_quality_issue_summary_names_reasons(self) -> None:
        summary = quiz_harness.part_quality_issue_summary(
            [
                {"markdown": "# Short", "quiz": [], "fallback_used": True},
                {"markdown": "x" * 950, "quiz": [{"question": "", "expected_answer": ""}]},
            ],
            [0, 1],
        )

        self.assertIn("fallback_used=true", summary)
        self.assertIn("quiz_empty", summary)
        self.assertIn("missing_question", summary)
        self.assertIn("missing_expected_answer", summary)

    def test_quality_check_allows_same_question_text_in_different_checkpoints(self) -> None:
        parts = [
            {
                "markdown": (f"Answer {idx} is supported by the checkpoint explanation. " * 30),
                "quiz": [{"question": "What is the key idea?", "expected_answer": f"Answer {idx}"}],
            }
            for idx in range(3)
        ]

        self.assertEqual(quiz_harness.find_part_quality_issues(parts), [])

    def test_quality_issues_detect_ungrounded_answer_and_empty_source_context(self) -> None:
        weak = quiz_harness.find_part_quality_issues([
            {
                "markdown": "AVL trees use rotations to restore balance. " * 30,
                "quiz": [
                    {
                        "question": "What color is the tree?",
                        "expected_answer": "Purple.",
                        "source_context": "",
                    }
                ],
            }
        ])
        summary = quiz_harness.part_quality_issue_summary([
            {
                "markdown": "AVL trees use rotations to restore balance. " * 30,
                "quiz": [
                    {
                        "question": "What color is the tree?",
                        "expected_answer": "Purple.",
                        "source_context": "",
                    }
                ],
            }
        ], [0])

        self.assertEqual(weak, [0])
        self.assertIn("answer_not_grounded", summary)
        self.assertIn("empty_source_context", summary)
        self.assertIn("What color is the tree?", summary)
        self.assertIn("Purple.", summary)

    def test_quality_check_normalizes_simple_plural_answers(self) -> None:
        weak = quiz_harness.find_part_quality_issues([
            {
                "markdown": "Keys can be strings, numbers, or immutable tuples. " * 30,
                "quiz": [
                    {
                        "question": "Which value can be a hashmap key?",
                        "expected_answer": "String",
                    }
                ],
            }
        ])

        self.assertEqual(weak, [])

    def test_normalize_quiz_uses_checkpoint_markdown_before_external_context(self) -> None:
        quiz = quiz_harness.normalize_quiz(
            [
                {
                    "question": "Which value can be a hashmap key?",
                    "options": ["List", "String", "Dictionary", "Set"],
                    "correct_option_index": 1,
                    "expected_answer": "String",
                }
            ],
            "hashmaps",
            {"title": "Hashmap basics", "difficulty": "easy", "goal": "Explain hashmap keys."},
            contexts=[{"doc": "Wikipedia", "chunk_id": "wiki_1", "text": "Hash tables abstract hashing."}],
            markdown="Keys can be strings, numbers, or immutable tuples.",
        )

        self.assertEqual(quiz[0]["source_context"], "Checkpoint markdown: Keys can be strings, numbers, or immutable tuples.")
        self.assertEqual(quiz_harness.find_part_quality_issues([{"markdown": "Keys can be strings, numbers, or immutable tuples. " * 30, "quiz": quiz}]), [])

    def test_checkpoint_question_mode_reflects_embedded_quiz(self) -> None:
        self.assertEqual(
            quiz_harness.checkpoint_question_mode([
                {"question_mode": "mcq"},
                {"question_mode": "mcq"},
            ]),
            "mcq",
        )
        self.assertEqual(
            quiz_harness.checkpoint_question_mode([
                {"question_mode": "open"},
                {"question_mode": "open"},
            ]),
            "open",
        )
        self.assertEqual(
            quiz_harness.checkpoint_question_mode([
                {"question_mode": "mcq"},
                {"question_mode": "open"},
            ]),
            "mixed",
        )

    def test_worker_search_executor_calls_search_web(self) -> None:
        import json

        fake_result = {"results": [{"title": "Test", "snippet": "Result"}], "provider_requests": 2}
        with mock.patch("backend.app.services.web_search.search_web_and_wikipedia", return_value=fake_result):
            result_str = lesson_harness._worker_search_executor("search_web", {"query": "AVL trees", "num_results": 3})
            result = json.loads(result_str)
        self.assertEqual(result["results"][0]["title"], "Test")

    def test_worker_search_executor_calls_search_wikipedia(self) -> None:
        import json

        fake_result = {"results": [{"title": "AVL tree", "snippet": "A self-balancing tree"}]}
        with mock.patch("backend.app.services.web_search.search_wikipedia", return_value=fake_result):
            result_str = lesson_harness._worker_search_executor("search_wikipedia", {"query": "AVL tree"})
            result = json.loads(result_str)
        self.assertEqual(result["results"][0]["title"], "AVL tree")

    def test_worker_search_executor_returns_error_on_empty_query(self) -> None:
        import json

        result_str = lesson_harness._worker_search_executor("search_web", {"query": ""})
        result = json.loads(result_str)
        self.assertIn("error", result)

    def test_worker_search_executor_returns_error_on_unknown_tool(self) -> None:
        import json

        result_str = lesson_harness._worker_search_executor("unknown_tool", {})
        result = json.loads(result_str)
        self.assertIn("error", result)

    def test_worker_search_tools_defined(self) -> None:
        names = {tool["function"]["name"] for tool in lesson_harness.WORKER_SEARCH_TOOLS}
        self.assertEqual(names, {"search_web", "search_wikipedia"})


if __name__ == "__main__":
    unittest.main()
