from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.api.common import require_account
from backend.app.main import app
from backend.app.schemas import Doc, Lesson


def _mock_require_account():
    return ("admin", None)


class UsageCountsTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[require_account] = _mock_require_account
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(require_account, None)

    def test_usage_uses_live_doc_and_lesson_counts(self) -> None:
        docs = [
            Doc(id="upload_d1", name="one.txt", uploaded_at="2026-06-14T00:00:00Z"),
            Doc(id="upload_d2", name="two.txt", uploaded_at="2026-06-14T00:00:00Z"),
            Doc(id="raw_d3", name="three.txt", uploaded_at="2026-06-14T00:00:00Z"),
        ]
        lessons = [
            Lesson(id="l1", title="One", objective="", content_blocks=[]),
            Lesson(id="l2", title="Two", objective="", content_blocks=[]),
            Lesson(id="l3", title="Three", objective="", content_blocks=[]),
        ]
        stale_usage = {
            "token_used": 123,
            "doc_count": 99,
            "lesson_count": 99,
            "storage_bytes": 456,
        }

        with (
            mock.patch("backend.app.api.usage.get_usage", return_value=stale_usage),
            mock.patch("backend.app.api.usage.list_docs", return_value=docs),
            mock.patch("backend.app.api.usage.list_account_lessons", return_value=lessons),
        ):
            resp = self.client.get("/usage", params={"account_id": "admin"})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["tokens_used"], 123)
        self.assertEqual(body["doc_count"], 3)
        self.assertEqual(body["lessons_count"], 3)


if __name__ == "__main__":
    unittest.main()
