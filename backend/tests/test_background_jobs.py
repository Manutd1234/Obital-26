from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.services.background_jobs import JsonJobStore


class BackgroundJobStoreTests(unittest.TestCase):
    def test_create_get_list_are_account_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = JsonJobStore(Path(td) / "jobs.json")
            admin_job = store.create_job(job_type="qa_generation", account_id="admin", job_id_prefix="job")
            other_job = store.create_job(job_type="qa_generation", account_id="other", job_id_prefix="job")

            self.assertEqual(store.get_job(job_id=admin_job["job_id"], account_id="admin")["job_id"], admin_job["job_id"])
            self.assertIsNone(store.get_job(job_id=admin_job["job_id"], account_id="other"))
            self.assertEqual([job["job_id"] for job in store.list_jobs(account_id="admin")], [admin_job["job_id"]])
            self.assertEqual([job["job_id"] for job in store.list_jobs(account_id="other")], [other_job["job_id"]])

    def test_subtask_update_and_cancellation_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = JsonJobStore(Path(td) / "jobs.json")
            job = store.create_job(
                job_type="lesson_generation",
                account_id="admin",
                job_id_prefix="lesson_job",
                fields={
                    "status": "queued",
                    "subtasks": [{"name": "planner", "status": "queued", "phase": "queued", "progress": 0}],
                },
            )
            store.set_subtask(job["job_id"], "planner", status="running", phase="collecting_context", progress=25)
            updated = store.get_job(job_id=job["job_id"], account_id="admin")
            self.assertEqual(updated["subtasks"][0]["status"], "running")
            self.assertEqual(updated["subtasks"][0]["progress"], 25)

            store.set_job(job["job_id"], status="canceled")
            revived = store.set_job(job["job_id"], status="succeeded", cancellation_terminal=True)
            self.assertEqual(revived["status"], "canceled")


if __name__ == "__main__":
    unittest.main()
