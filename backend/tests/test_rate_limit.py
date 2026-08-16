from __future__ import annotations

import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from backend.app.api.common import require_account
from backend.app.main import app
from backend.app.services.models import Base
from engine.quiz.quiz_backend import QuizBuildResponse


def _mock_require_account():
    return ("testuser", None)


class RateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        from backend.app.api import rate_limit as rl_mod

        rl_mod._BUCKETS.clear()

    def test_quiz_build_returns_429_after_limit_exceeded(self) -> None:
        fake_quiz = QuizBuildResponse(
            quiz_id="q1",
            topic_id="t1",
            topic_title="Test",
            questions=[],
            build_indicator="ok",
        )

        app.dependency_overrides[require_account] = _mock_require_account
        client = TestClient(app)
        try:
            with mock.patch(
                "backend.app.services.quiz_harness.generate_quiz",
                autospec=True,
                return_value=fake_quiz,
            ):
                payload = {
                    "topic": "test",
                    "num_questions": 3,
                    "question_mode": "mcq",
                }
                for i in range(5):
                    resp = client.post("/quiz/build", json=payload)
                    self.assertEqual(
                        resp.status_code, 200,
                        f"Request {i + 1} expected 200, got {resp.status_code}: {resp.text}",
                    )

                blocked = client.post("/quiz/build", json=payload)
                self.assertEqual(blocked.status_code, 429)
                self.assertIn("quiz_build", blocked.json()["detail"])
        finally:
            app.dependency_overrides.pop(require_account, None)

    def test_pipeline_returns_429_after_limit_exceeded(self) -> None:
        app.dependency_overrides[require_account] = _mock_require_account
        client = TestClient(app)
        try:
            with mock.patch(
                "backend.app.api.pipeline.create_qa_generation_job",
                return_value={
                    "job_id": "j1",
                    "type": "qa_generation",
                    "status": "queued",
                    "progress": 0,
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "error": None,
                    "result": None,
                },
            ):
                for i in range(2):
                    resp = client.post("/pipeline/jobs/qa-generation")
                    self.assertIn(
                        resp.status_code, (200, 201),
                        f"Request {i + 1} unexpected: {resp.status_code}: {resp.text}",
                    )

                blocked = client.post("/pipeline/jobs/qa-generation")
                self.assertEqual(blocked.status_code, 429)
                self.assertIn("pipeline", blocked.json()["detail"])
        finally:
            app.dependency_overrides.pop(require_account, None)

    def test_db_rate_limit_survives_empty_in_process_buckets(self) -> None:
        from backend.app.api import rate_limit as rl_mod

        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

        with mock.patch("backend.app.api.rate_limit.db_enabled", return_value=True), \
             mock.patch("backend.app.api.rate_limit.SessionLocal", new=SessionLocal):
            for _ in range(5):
                rl_mod._check_and_increment("testuser", "quiz_build")
                rl_mod._BUCKETS.clear()

            with self.assertRaises(Exception) as ctx:
                rl_mod._check_and_increment("testuser", "quiz_build")

        self.assertEqual(getattr(ctx.exception, "status_code", None), 429)


if __name__ == "__main__":
    unittest.main()
