from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest import mock

from backend.app.schemas import Doc, DocChunk, Lesson
from backend.app.services import chat_tools
from backend.app.services.chat_tools import (
    try_handle_tool_command,
    _extract_lesson_topic,
    _extract_quiz_topic,
    _is_dashboard_request,
    _is_lesson_request,
    _is_start_lesson_request,
    _is_docs_request,
    _is_help_request,
    create_lesson_payload,
)


def _make_lesson(lesson_id: str, title: str, status: str = "active", checkpoint: int = 0, total: int = 3) -> Lesson:
    return Lesson(
        id=lesson_id,
        title=title,
        objective=f"Learn {title}",
        content_blocks=[],
        status=status,
        current_checkpoint=checkpoint,
        total_checkpoints=total,
    )


def _make_doc(doc_id: str, name: str, text: str = "sample text") -> Doc:
    return Doc(
        id=doc_id,
        name=name,
        uploaded_at=datetime.now(UTC),
        chunks=[DocChunk(chunk_id=f"{doc_id}_chunk_0", text=text)],
    )


class IntentDetectionTests(unittest.TestCase):
    """Tests for natural language intent detection helpers."""

    def test_dashboard_exact_match(self) -> None:
        self.assertTrue(_is_dashboard_request("dashboard"))
        self.assertTrue(_is_dashboard_request("/dashboard"))

    def test_dashboard_phrases(self) -> None:
        for phrase in [
            "show my dashboard",
            "my stats",
            "show stats",
            "my progress",
            "show progress",
            "how am i doing",
            "what are my stats",
        ]:
            self.assertTrue(_is_dashboard_request(phrase), f"Expected True for: {phrase}")

    def test_dashboard_non_match(self) -> None:
        self.assertFalse(_is_dashboard_request("start lesson on graphs"))
        self.assertFalse(_is_dashboard_request("help"))

    def test_lesson_exact_match(self) -> None:
        self.assertTrue(_is_lesson_request("lessons"))
        self.assertTrue(_is_lesson_request("my lessons"))
        self.assertTrue(_is_lesson_request("show lessons"))

    def test_lesson_phrases(self) -> None:
        for phrase in [
            "show my lessons",
            "what lessons do i have",
            "list lessons",
            "lesson status",
            "continue lesson",
            "resume lesson",
        ]:
            self.assertTrue(_is_lesson_request(phrase), f"Expected True for: {phrase}")

    def test_start_lesson_phrases(self) -> None:
        for phrase in [
            "start lesson on binary trees",
            "give me a lesson on hashing",
            "create a lesson about graphs",
            "new lesson on arrays",
            "teach me about linked lists",
            "teach me dynamic programming",
        ]:
            self.assertTrue(_is_start_lesson_request(phrase), f"Expected True for: {phrase}")

    def test_docs_exact_match(self) -> None:
        self.assertTrue(_is_docs_request("docs"))
        self.assertTrue(_is_docs_request("my docs"))
        self.assertTrue(_is_docs_request("show documents"))

    def test_docs_phrases(self) -> None:
        for phrase in [
            "show my docs",
            "list documents",
            "what documents do i have",
            "uploaded docs",
        ]:
            self.assertTrue(_is_docs_request(phrase), f"Expected True for: {phrase}")

    def test_help_exact_match(self) -> None:
        self.assertTrue(_is_help_request("help"))
        self.assertTrue(_is_help_request("/help"))
        self.assertTrue(_is_help_request("what can you do"))
        self.assertTrue(_is_help_request("commands"))


class ExtractLessonTopicTests(unittest.TestCase):
    """Tests for _extract_lesson_topic regex extraction."""

    def test_start_lesson_on_topic(self) -> None:
        self.assertEqual(_extract_lesson_topic("start lesson on binary trees"), "binary trees")

    def test_give_me_a_lesson(self) -> None:
        self.assertEqual(_extract_lesson_topic("give me a lesson on hashing"), "hashing")

    def test_create_a_lesson(self) -> None:
        self.assertEqual(_extract_lesson_topic("create a lesson about graphs"), "graphs")

    def test_lesson_about(self) -> None:
        self.assertEqual(_extract_lesson_topic("lesson about arrays"), "arrays")

    def test_teach_me_about(self) -> None:
        self.assertEqual(_extract_lesson_topic("teach me about linked lists"), "linked lists")

    def test_teach_me_topic(self) -> None:
        self.assertEqual(_extract_lesson_topic("teach me dynamic programming"), "dynamic programming")

    def test_no_match_returns_none(self) -> None:
        self.assertIsNone(_extract_lesson_topic("hello world"))
        self.assertIsNone(_extract_lesson_topic("show my dashboard"))
        self.assertIsNone(_extract_lesson_topic("help"))

    def test_strips_trailing_punctuation(self) -> None:
        self.assertEqual(_extract_lesson_topic("start lesson on graphs."), "graphs")
        self.assertEqual(_extract_lesson_topic("teach me about arrays!"), "arrays")

    def test_case_insensitive(self) -> None:
        self.assertEqual(_extract_lesson_topic("START LESSON ON TREES"), "TREES")
        self.assertEqual(_extract_lesson_topic("Teach Me About Heaps"), "Heaps")


class ExtractQuizTopicTests(unittest.TestCase):
    """Tests for _extract_quiz_topic regex extraction."""

    def test_quiz_topic_with_standard_prepositions(self) -> None:
        self.assertEqual(_extract_quiz_topic("quiz me on binary trees"), "binary trees")
        self.assertEqual(_extract_quiz_topic("create a quiz about graph search"), "graph search")

    def test_quiz_topic_with_common_chat_phrasings(self) -> None:
        self.assertEqual(_extract_quiz_topic("create a quiz for photosynthesis"), "photosynthesis")
        self.assertEqual(_extract_quiz_topic("start quiz covering recursion"), "recursion")
        self.assertEqual(_extract_quiz_topic("give me a quiz over derivatives"), "derivatives")
        self.assertEqual(_extract_quiz_topic("quiz: cellular respiration"), "cellular respiration")
        self.assertEqual(_extract_quiz_topic("quiz me linked lists"), "linked lists")

    def test_quiz_topic_strips_trailing_punctuation(self) -> None:
        self.assertEqual(_extract_quiz_topic("create a quiz for graphs."), "graphs")
        self.assertEqual(_extract_quiz_topic("quiz: arrays!"), "arrays")


class SlashCommandTests(unittest.TestCase):
    """Tests for slash command routing via try_handle_tool_command."""

    @mock.patch("backend.app.services.chat_tools._handle_dashboard")
    def test_slash_dashboard(self, mock_dashboard) -> None:
        mock_dashboard.return_value = "Dashboard output"
        result = try_handle_tool_command("/dashboard", account_id="admin")
        self.assertEqual(result, "Dashboard output")
        mock_dashboard.assert_called_once()

    @mock.patch("backend.app.services.chat_tools._handle_lessons")
    def test_slash_lessons(self, mock_lessons) -> None:
        mock_lessons.return_value = "Lessons output"
        result = try_handle_tool_command("/lessons", account_id="admin")
        self.assertEqual(result, "Lessons output")

    @mock.patch("backend.app.services.chat_tools._handle_docs")
    def test_slash_docs(self, mock_docs) -> None:
        mock_docs.return_value = "Docs output"
        result = try_handle_tool_command("/docs", account_id="admin")
        self.assertEqual(result, "Docs output")

    @mock.patch("backend.app.services.chat_tools._handle_help")
    def test_slash_help(self, mock_help) -> None:
        mock_help.return_value = "Help output"
        result = try_handle_tool_command("/help", account_id="admin")
        self.assertEqual(result, "Help output")

    def test_slash_review_returns_info(self) -> None:
        result = try_handle_tool_command("/review", account_id="admin")
        self.assertIn("quiz", result.lower())

    def test_slash_explain_returns_info(self) -> None:
        result = try_handle_tool_command("/explain", account_id="admin")
        self.assertIn("quiz", result.lower())

    def test_unknown_slash_command(self) -> None:
        result = try_handle_tool_command("/foobar", account_id="admin")
        self.assertIn("Unknown command", result)
        self.assertIn("/foobar", result)


class NaturalLanguageRoutingTests(unittest.TestCase):
    """Tests for natural language routing via try_handle_tool_command."""

    def test_empty_text_returns_none(self) -> None:
        self.assertIsNone(try_handle_tool_command("", account_id="admin"))
        self.assertIsNone(try_handle_tool_command("   ", account_id="admin"))

    def test_non_matching_text_returns_none(self) -> None:
        self.assertIsNone(try_handle_tool_command("hello there", account_id="admin"))

    @mock.patch("backend.app.services.chat_tools._handle_dashboard")
    def test_dashboard_natural_language(self, mock_dashboard) -> None:
        mock_dashboard.return_value = "Dashboard"
        result = try_handle_tool_command("show my stats", account_id="admin")
        self.assertEqual(result, "Dashboard")
        mock_dashboard.assert_called_once()

    @mock.patch("backend.app.services.chat_tools._handle_lessons")
    def test_lessons_natural_language(self, mock_lessons) -> None:
        mock_lessons.return_value = "Lessons"
        result = try_handle_tool_command("show my lessons", account_id="admin")
        self.assertEqual(result, "Lessons")

    @mock.patch("backend.app.services.chat_tools._handle_docs")
    def test_docs_natural_language(self, mock_docs) -> None:
        mock_docs.return_value = "Docs"
        result = try_handle_tool_command("show my docs", account_id="admin")
        self.assertEqual(result, "Docs")

    @mock.patch("backend.app.services.chat_tools._handle_help")
    def test_help_natural_language(self, mock_help) -> None:
        mock_help.return_value = "Help"
        result = try_handle_tool_command("what can you do", account_id="admin")
        self.assertEqual(result, "Help")

    @mock.patch("backend.app.services.chat_tools.create_lesson_payload")
    def test_start_lesson_natural_language(self, mock_create) -> None:
        mock_create.return_value = {
            "lesson_id": "lesson_binary_trees",
            "title": "Lesson: binary trees",
            "topic": "binary trees",
            "checkpoints": [
                {"title": "binary trees fundamentals", "difficulty": "easy", "num_questions": 2, "pass_threshold_percent": 60.0},
                {"title": "binary trees application", "difficulty": "medium", "num_questions": 3, "pass_threshold_percent": 70.0},
                {"title": "binary trees mastery", "difficulty": "hard", "num_questions": 3, "pass_threshold_percent": 75.0},
            ],
            "current_checkpoint_index": 0,
        }
        result = try_handle_tool_command("start lesson on binary trees", account_id="admin")
        self.assertIn("binary trees", result)
        self.assertIn("Started lesson", result)
        mock_create.assert_called_once_with(topic="binary trees", account_id="admin", use_harness=True)

    @mock.patch("backend.app.services.chat_tools.create_lesson_payload")
    def test_start_lesson_natural_language_harness_job(self, mock_create) -> None:
        mock_create.return_value = {
            "lesson_id": None,
            "title": "Lesson: binary trees",
            "topic": "binary trees",
            "checkpoints": [],
            "current_checkpoint_index": 0,
            "harness_job_id": "lesson_job_abc123",
            "harness_status": "queued",
        }
        result = try_handle_tool_command("start lesson on binary trees", account_id="admin")
        self.assertIn("binary trees", result)
        self.assertIn("Generating rich lesson", result)
        self.assertIn("lesson_job_abc123", result)

    @mock.patch("backend.app.services.chat_tools._handle_quiz")
    def test_create_quiz_for_topic_routes_to_quiz_handler(self, mock_quiz) -> None:
        mock_quiz.return_value = "Quiz output"
        result = try_handle_tool_command("create a quiz for binary trees", account_id="admin")
        self.assertEqual(result, "Quiz output")
        mock_quiz.assert_called_once_with(topic="binary trees", account_id="admin", user_dek=None)


class DashboardHandlerTests(unittest.TestCase):
    """Tests for _handle_dashboard."""

    @mock.patch("backend.app.services.chat_tools.get_rewards_dashboard")
    def test_dashboard_with_data(self, mock_dashboard) -> None:
        mock_dashboard.return_value = {
            "summary": {
                "total_points": 150,
                "total_quizzes": 5,
                "accuracy_percent": 80.0,
                "total_correct": 16,
                "total_answered": 20,
            },
            "trend": [
                {"quiz_id": "q1", "score_percent": 90.0, "points_earned": 30},
                {"quiz_id": "q2", "score_percent": 70.0, "points_earned": 20},
            ],
        }
        result = try_handle_tool_command("/dashboard", account_id="admin")
        self.assertIn("150", result)
        self.assertIn("5", result)
        self.assertIn("80.0%", result)
        self.assertIn("16/20", result)
        self.assertIn("q1", result)

    @mock.patch("backend.app.services.chat_tools.get_rewards_dashboard")
    def test_dashboard_empty(self, mock_dashboard) -> None:
        mock_dashboard.return_value = {"summary": {}, "trend": []}
        result = try_handle_tool_command("/dashboard", account_id="admin")
        self.assertIn("0", result)
        self.assertIn("No quiz history", result)


class LessonsHandlerTests(unittest.TestCase):
    """Tests for _handle_lessons."""

    @mock.patch("backend.app.services.chat_tools.list_lessons")
    @mock.patch("backend.app.services.chat_tools.list_account_lessons")
    def test_lessons_with_data(self, mock_account, mock_qa) -> None:
        mock_account.return_value = [_make_lesson("l1", "Graphs", "active", 1, 3)]
        mock_qa.return_value = [_make_lesson("l2", "Trees", "completed", 3, 3)]
        result = try_handle_tool_command("/lessons", account_id="admin")
        self.assertIn("Graphs", result)
        self.assertIn("Trees", result)
        self.assertIn("active", result)

    @mock.patch("backend.app.services.chat_tools.list_lessons")
    @mock.patch("backend.app.services.chat_tools.list_account_lessons")
    def test_lessons_empty(self, mock_account, mock_qa) -> None:
        mock_account.return_value = []
        mock_qa.return_value = []
        result = try_handle_tool_command("/lessons", account_id="admin")
        self.assertIn("No lessons found", result)


class DocsHandlerTests(unittest.TestCase):
    """Tests for _handle_docs."""

    @mock.patch("backend.app.services.chat_tools.list_docs")
    def test_docs_with_data(self, mock_docs) -> None:
        mock_docs.return_value = [_make_doc("d1", "notes.txt"), _make_doc("d2", "slides.pdf")]
        result = try_handle_tool_command("/docs", account_id="admin")
        self.assertIn("notes.txt", result)
        self.assertIn("slides.pdf", result)
        self.assertIn("2 document(s)", result)

    @mock.patch("backend.app.services.chat_tools.list_docs")
    def test_docs_empty(self, mock_docs) -> None:
        mock_docs.return_value = []
        result = try_handle_tool_command("/docs", account_id="admin")
        self.assertIn("No documents", result)


class DocSearchHandlerTests(unittest.TestCase):
    """Tests for /search command."""

    @mock.patch("backend.app.services.storage.search_docs")
    def test_search_with_results(self, mock_search) -> None:
        mock_search.return_value = [_make_doc("d1", "physics_notes.txt")]
        result = try_handle_tool_command("/search physics", account_id="admin")
        self.assertIn("physics_notes.txt", result)

    @mock.patch("backend.app.services.storage.search_docs")
    def test_search_no_results(self, mock_search) -> None:
        mock_search.return_value = []
        result = try_handle_tool_command("/search nonexistent", account_id="admin")
        self.assertIn("No documents matched", result)

    def test_search_empty_query(self) -> None:
        result = try_handle_tool_command("/search", account_id="admin")
        self.assertIn("Usage", result)


class SettingsHandlerTests(unittest.TestCase):
    """Tests for /settings command."""

    @mock.patch("backend.app.services.chat_tools.get_settings")
    def test_settings_get(self, mock_get) -> None:
        mock_get.return_value = mock.Mock(chat=mock.Mock(system_prompt="Be helpful."))
        result = try_handle_tool_command("/settings", account_id="admin")
        self.assertIn("Be helpful.", result)

    @mock.patch("backend.app.services.chat_tools.get_settings")
    def test_settings_get_explicit(self, mock_get) -> None:
        mock_get.return_value = mock.Mock(chat=mock.Mock(system_prompt="Be concise."))
        result = try_handle_tool_command("/settings get", account_id="admin")
        self.assertIn("Be concise.", result)

    @mock.patch("backend.app.services.chat_tools.update_settings")
    def test_settings_set(self, mock_update) -> None:
        mock_update.return_value = mock.Mock(chat=mock.Mock(system_prompt="New prompt"))
        result = try_handle_tool_command("/settings set system_prompt New prompt", account_id="admin")
        self.assertIn("updated", result.lower())
        self.assertIn("New prompt", result)

    def test_settings_set_empty_prompt(self) -> None:
        result = try_handle_tool_command("/settings set system_prompt", account_id="admin")
        self.assertIn("Unknown settings command", result)

    def test_settings_unknown_subcommand(self) -> None:
        result = try_handle_tool_command("/settings foobar", account_id="admin")
        self.assertIn("Unknown settings command", result)


class CreateLessonPayloadTests(unittest.TestCase):
    """Tests for create_lesson_payload."""

    @mock.patch("backend.app.services.lesson_harness.create_lesson_generation_job")
    def test_create_lesson_basic(self, mock_job) -> None:
        mock_job.return_value = {"job_id": "lesson_job_binary", "status": "queued"}

        payload = create_lesson_payload(topic="binary trees", account_id="admin")

        self.assertIsNone(payload["lesson_id"])
        self.assertEqual(payload["title"], "Lesson: binary trees")
        self.assertEqual(payload["topic"], "binary trees")
        self.assertEqual(payload["checkpoints"], [])
        self.assertEqual(payload["harness_job_id"], "lesson_job_binary")
        self.assertEqual(payload["harness_status"], "queued")

    @mock.patch("backend.app.services.lesson_harness.create_lesson_generation_job")
    def test_create_lesson_custom_title(self, mock_job) -> None:
        mock_job.return_value = {"job_id": "lesson_job_graphs", "status": "queued"}

        payload = create_lesson_payload(topic="graphs", account_id="admin", title="Graph Theory 101")
        self.assertEqual(payload["title"], "Graph Theory 101")

    def test_create_lesson_empty_topic_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            create_lesson_payload(topic="", account_id="admin")

    def test_create_lesson_legacy_path_disabled(self) -> None:
        with self.assertRaises(RuntimeError):
            create_lesson_payload(topic="trees", account_id="admin", use_harness=False)

    @mock.patch("backend.app.services.lesson_harness.create_lesson_generation_job")
    def test_create_lesson_harness_error(self, mock_job) -> None:
        mock_job.side_effect = RuntimeError("Harness unavailable")
        with self.assertRaises(RuntimeError):
            create_lesson_payload(topic="trees", account_id="admin", use_harness=True)

    @mock.patch("backend.app.services.lesson_harness.create_lesson_generation_job")
    def test_create_lesson_harness_success(self, mock_job) -> None:
        mock_job.return_value = {"job_id": "lesson_job_abc123", "status": "queued"}
        payload = create_lesson_payload(topic="trees", account_id="admin", use_harness=True)
        self.assertIsNone(payload["lesson_id"])
        self.assertEqual(payload["topic"], "trees")
        self.assertEqual(payload["harness_job_id"], "lesson_job_abc123")
        self.assertEqual(payload["harness_status"], "queued")
        self.assertEqual(len(payload["checkpoints"]), 0)


class HelpHandlerTests(unittest.TestCase):
    """Tests for /help command."""

    def test_help_output(self) -> None:
        result = try_handle_tool_command("/help", account_id="admin")
        self.assertIn("Dashboard", result)
        self.assertIn("Lessons", result)
        self.assertIn("Documents", result)
        self.assertIn("Quiz", result)
        self.assertIn("Settings", result)


if __name__ == "__main__":
    unittest.main()
