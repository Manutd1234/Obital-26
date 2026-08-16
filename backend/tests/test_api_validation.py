from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.schemas import Lesson
from backend.app.api.common import require_account


def _mock_require_account():
    return ("admin", None)


class ApiValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_invalid_account_id_returns_422(self) -> None:
        resp = self.client.get("/docs", params={"account_id": "Admin"})
        self.assertEqual(resp.status_code, 422)

    def test_resolve_lesson_accepts_natural_language_query(self) -> None:
        app.dependency_overrides[require_account] = _mock_require_account
        try:
            with mock.patch("backend.app.api.lesson.get_lesson") as get_lesson_mock:
                get_lesson_mock.return_value = Lesson(
                    id="lesson_trees",
                    title="Trees",
                    objective="",
                    content_blocks=[],
                )
                resp = self.client.get("/lessons/resolve", params={"q": "lesson on tree", "account_id": "admin"})

            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["id"], "lesson_trees")
        finally:
            app.dependency_overrides.pop(require_account, None)

    def test_db_health_endpoint_works_without_database_url(self) -> None:
        resp = self.client.get("/health/db")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("enabled", body)
        self.assertIn("ok", body)
        self.assertIn("status", body)


if __name__ == "__main__":
    unittest.main()
