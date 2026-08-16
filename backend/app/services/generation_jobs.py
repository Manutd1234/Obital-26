from __future__ import annotations

import threading
import time
from typing import Literal

from backend.app.services.background_jobs import JsonJobStore
from backend.app.services.storage import DATA_DIR

JobStatus = Literal["queued", "running", "succeeded", "failed"]

_JOBS_PATH = DATA_DIR / "generation_jobs.json"
_jobs: dict[str, dict] | None = None
_JOB_STORE = JsonJobStore(_JOBS_PATH)


def _job_store() -> JsonJobStore:
    global _JOB_STORE, _jobs
    if _JOB_STORE.path != _JOBS_PATH:
        _JOB_STORE = JsonJobStore(_JOBS_PATH)
    if _jobs is None:
        _JOB_STORE.reset_cache()
        _jobs = _JOB_STORE._load_unlocked()
    elif _JOB_STORE._jobs is not _jobs:
        _JOB_STORE._jobs = _jobs
    return _JOB_STORE


def _load_jobs() -> dict[str, dict]:
    return _job_store()._load_unlocked()


def _save_jobs() -> None:
    _job_store()._save_unlocked()


def _set_job(job_id: str, **updates) -> dict:
    return _job_store().set_job(job_id, **updates)


def create_qa_generation_job(account_id: str, user_dek: bytes | None = None) -> dict:
    job = _job_store().create_job(job_type="qa_generation", account_id=account_id, job_id_prefix="job")
    job_id = str(job["job_id"])
    thread = threading.Thread(
        target=_run_qa_generation_job,
        kwargs={"job_id": job_id, "account_id": account_id, "user_dek": user_dek},
        daemon=True,
        name=f"qa-job-{job_id}",
    )
    thread.start()
    return job


def _run_qa_generation_job(*, job_id: str, account_id: str, user_dek: bytes | None) -> None:
    _set_job(job_id, status="running", progress=10)
    try:
        from backend.app.services import chat_tools

        qa = chat_tools._try_generate_qa(account_id, user_dek)
        if qa is None:
            _set_job(job_id, status="running", progress=35)
            deadline = time.monotonic() + 300.0
            while time.monotonic() < deadline:
                cached = chat_tools._qa_cache.get(account_id)
                if cached is not None:
                    qa = cached
                    break
                time.sleep(1.0)
            if qa is None:
                _set_job(
                    job_id,
                    status="failed",
                    progress=100,
                    error="QA generation did not complete before the job timeout.",
                )
                return
        _set_job(
            job_id,
            status="succeeded",
            progress=100,
            result={
                "topics": len(getattr(qa.curriculum, "topics", []) or []),
                "qa_sets": len(getattr(qa, "qa_sets", []) or []),
                "artifact": "output/output_curriculum_qa.json",
            },
        )
    except Exception as exc:
        _set_job(job_id, status="failed", progress=100, error=str(exc))


def get_generation_job(job_id: str, account_id: str) -> dict | None:
    return _job_store().get_job(job_id=job_id, account_id=account_id)
