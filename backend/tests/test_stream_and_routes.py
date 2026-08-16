"""Route-level smoke tests and frontend stream parser logic tests."""
from __future__ import annotations

import json
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.api.chat import _sse_stream
from backend.app.api.common import require_account
from backend.app.schemas import ChatSession


def _mock_require_account():
    return ("admin", None)


class RouteSmokeTests(unittest.TestCase):
    """Verify new endpoints are exposed and return correct HTTP methods."""

    def setUp(self) -> None:
        app.dependency_overrides[require_account] = _mock_require_account
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(require_account, None)

    def test_chat_messages_stream_exists(self) -> None:
        with mock.patch("backend.app.services.chat_service.get_session", return_value=ChatSession(id="s1")):
            resp = self.client.post(
                "/chat/sessions/s1/messages/stream",
                params={"account_id": "admin"},
                json={"message": "hello"},
            )
        # Should not be 404 or 405 — endpoint exists
        self.assertNotIn(resp.status_code, [404, 405])

    def test_docs_search_exists(self) -> None:
        resp = self.client.post(
            "/docs/search",
            params={"account_id": "admin"},
            json={"query_vector": [0.0] * 384, "top_k": 3, "threshold": 0.35},
        )
        # Should not be 404 or 405
        self.assertNotIn(resp.status_code, [404, 405])

    def test_docs_search_rejects_wrong_dim(self) -> None:
        resp = self.client.post(
            "/docs/search",
            params={"account_id": "admin"},
            json={"query_vector": [0.0] * 100, "top_k": 3},
        )
        self.assertEqual(resp.status_code, 422)

    def test_docs_embeddings_exists(self) -> None:
        with mock.patch("backend.app.services.storage.get_doc", return_value=None):
            resp = self.client.put(
                "/docs/nonexistent/embeddings",
                params={"account_id": "admin"},
                json={"chunks": []},
            )
        # Should not be 404 from route (doc not found is expected 404)
        self.assertIn(resp.status_code, [200, 404])

    def test_lesson_generate_exists(self) -> None:
        fake_job = {
            "job_id": "lesson_job_1",
            "type": "lesson_generation",
            "status": "queued",
            "phase": "queued",
            "progress": 0,
            "created_at": "2026-06-13T00:00:00+00:00",
            "updated_at": "2026-06-13T00:00:00+00:00",
            "error": None,
            "rejection_reason": None,
            "result": None,
        }
        with mock.patch("backend.app.api.lesson.create_lesson_generation_job", return_value=fake_job):
            resp = self.client.post(
                "/lessons/generate",
                params={"account_id": "admin"},
                json={"topic": "AVL trees"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["type"], "lesson_generation")

    @mock.patch("backend.app.api.lesson.grade_lesson_quiz")
    @mock.patch("backend.app.api.lesson._account_store")
    def test_lesson_grade_rejects_negative_checkpoint_index(self, mock_store_fn, mock_grade) -> None:
        mock_store = mock.Mock()
        mock_store.get_active_lesson.return_value = {
            "lesson_id": "lesson_trees",
            "topic": "trees",
            "checkpoints": [{"pass_threshold_percent": 60.0, "quiz": [{"question": "Q?"}]}],
        }
        mock_store_fn.return_value = mock_store

        resp = self.client.post(
            "/lessons/lesson_trees/checkpoints/-1/grade",
            json={"answers": [{"question_id": "q1", "answer": "A"}]},
        )

        self.assertIn(resp.status_code, [404, 422])
        mock_grade.assert_not_called()
        mock_store.apply_lesson_attempt.assert_not_called()


class StreamEventParserTests(unittest.TestCase):
    """Test the SSE event parsing logic used by handleTutorChat.

    These are pure JS logic tests — they verify the data format expected
    by the frontend stream parser without requiring a browser.
    """

    def _parse_sse_lines(self, data_str: str) -> list[dict]:
        """Simulate the frontend SSE parsing logic."""
        events = []
        buffer = ""
        for chunk in data_str.split("\n"):
            buffer += chunk + "\n"
            lines = buffer.split("\n")
            buffer = lines.pop()
            for line in lines:
                if line.startswith("data: "):
                    try:
                        event = json.loads(line[6:])
                        events.append(event)
                    except json.JSONDecodeError:
                        pass
        return events

    @mock.patch("backend.app.api.chat.send_chat_message_stream", side_effect=OSError("resource unavailable"))
    def test_os_error_is_reported_as_retryable_resource_error(self, _mock_stream) -> None:
        lines = "".join(_sse_stream("s1", mock.Mock(), "admin", None))
        events = self._parse_sse_lines(lines)
        self.assertEqual(events[0]["event"], "error")
        self.assertIn("system resources", events[0]["data"])
        self.assertNotIn("OSError", events[0]["data"])
        self.assertEqual(events[-1]["event"], "done")

    def test_token_event(self) -> None:
        data = 'data: {"event": "token", "data": "Hello"}\n\n'
        events = self._parse_sse_lines(data)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "token")
        self.assertEqual(events[0]["data"], "Hello")

    def test_tool_event(self) -> None:
        data = 'data: {"event": "tool", "data": "Executed tool: list_lessons"}\n\n'
        events = self._parse_sse_lines(data)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "tool")

    def test_done_event(self) -> None:
        data = 'data: {"event": "done", "data": ""}\n\n'
        events = self._parse_sse_lines(data)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "done")

    def test_error_event(self) -> None:
        data = 'data: {"event": "error", "data": "Session not found"}\n\n'
        events = self._parse_sse_lines(data)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "error")
        self.assertEqual(events[0]["data"], "Session not found")

    def test_multiple_events(self) -> None:
        data = (
            'data: {"event": "token", "data": "He"}\n\n'
            'data: {"event": "token", "data": "llo"}\n\n'
            'data: {"event": "done", "data": ""}\n\n'
        )
        events = self._parse_sse_lines(data)
        self.assertEqual(len(events), 3)
        self.assertEqual("".join(e["data"] for e in events if e["event"] == "token"), "Hello")

    def test_malformed_json_skipped(self) -> None:
        data = 'data: not json\n\ndata: {"event": "token", "data": "ok"}\n\n'
        events = self._parse_sse_lines(data)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"], "ok")

    def test_partial_chunk_handled(self) -> None:
        """Simulate data arriving in chunks with a stateful buffer."""
        buffer = ""
        events = []

        def feed(data: str) -> list[dict]:
            nonlocal buffer
            buffer += data
            lines = buffer.split("\n")
            buffer = lines.pop()
            for line in lines:
                if line.startswith("data: "):
                    try:
                        events.append(json.loads(line[6:]))
                    except json.JSONDecodeError:
                        pass
            return events

        feed('data: {"event": "tok')
        self.assertEqual(len(events), 0)  # incomplete, buffered
        feed('en", "data": "X"}\n\n')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"], "X")


class VectorSearchValidationTests(unittest.TestCase):
    """Test Pydantic validation on VectorSearchRequest."""

    def setUp(self) -> None:
        app.dependency_overrides[require_account] = _mock_require_account

    def tearDown(self) -> None:
        app.dependency_overrides.pop(require_account, None)

    def test_rejects_wrong_vector_length(self) -> None:
        client = TestClient(app)
        resp = client.post(
            "/docs/search",
            params={"account_id": "admin"},
            json={"query_vector": [0.1, 0.2, 0.3], "top_k": 3},
        )
        self.assertEqual(resp.status_code, 422)

    def test_rejects_negative_top_k(self) -> None:
        client = TestClient(app)
        resp = client.post(
            "/docs/search",
            params={"account_id": "admin"},
            json={"query_vector": [0.0] * 384, "top_k": -1},
        )
        self.assertEqual(resp.status_code, 422)

    def test_rejects_threshold_out_of_range(self) -> None:
        client = TestClient(app)
        resp = client.post(
            "/docs/search",
            params={"account_id": "admin"},
            json={"query_vector": [0.0] * 384, "threshold": 2.0},
        )
        self.assertEqual(resp.status_code, 422)

    def test_accepts_valid_request(self) -> None:
        client = TestClient(app)
        resp = client.post(
            "/docs/search",
            params={"account_id": "admin"},
            json={"query_vector": [0.0] * 384, "top_k": 5, "threshold": 0.5},
        )
        self.assertEqual(resp.status_code, 200)
