from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.api.common import require_account
from backend.app.main import app
from backend.app.services import generation_jobs


def _mock_require_account():
    return ("admin", None)


class GenerationJobTests(unittest.TestCase):
    def setUp(self) -> None:
        generation_jobs._jobs = None

    def tearDown(self) -> None:
        generation_jobs._jobs = None

    def test_create_job_records_success(self) -> None:
        fake_qa = SimpleNamespace(
            curriculum=SimpleNamespace(topics=[object(), object()]),
            qa_sets=[object()],
        )
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(generation_jobs, "_JOBS_PATH", Path(td) / "jobs.json"):
                with mock.patch("backend.app.services.chat_tools._try_generate_qa", return_value=fake_qa):
                    with mock.patch("threading.Thread.start", lambda self: self._target(**self._kwargs)):
                        job = generation_jobs.create_qa_generation_job("admin")
                        saved = generation_jobs.get_generation_job(job["job_id"], "admin")

        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(saved["progress"], 100)
        self.assertEqual(saved["result"]["topics"], 2)
        self.assertEqual(saved["result"]["qa_sets"], 1)

    def test_get_job_endpoint(self) -> None:
        app.dependency_overrides[require_account] = _mock_require_account
        client = TestClient(app)
        try:
            with tempfile.TemporaryDirectory() as td:
                with mock.patch.object(generation_jobs, "_JOBS_PATH", Path(td) / "jobs.json"):
                    generation_jobs._jobs = {
                        "job_1": {
                            "job_id": "job_1",
                            "type": "qa_generation",
                            "status": "succeeded",
                            "progress": 100,
                            "account_id": "admin",
                            "created_at": "2026-06-12T00:00:00+00:00",
                            "updated_at": "2026-06-12T00:00:00+00:00",
                            "error": None,
                            "result": {"artifact": "output/output_curriculum_qa.json"},
                        }
                    }
                    resp = client.get("/pipeline/jobs/job_1")
        finally:
            app.dependency_overrides.pop(require_account, None)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["job_id"], "job_1")
        self.assertEqual(resp.json()["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
