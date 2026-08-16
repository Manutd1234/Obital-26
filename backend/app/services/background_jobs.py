from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class JsonJobStore:
    """Small JSON-backed job store for background API tasks."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] | None = None

    def reset_cache(self) -> None:
        with self._lock:
            self._jobs = None

    def _load_unlocked(self) -> dict[str, dict[str, Any]]:
        if self._jobs is not None:
            return self._jobs
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._jobs = raw if isinstance(raw, dict) else {}
            except Exception:
                self._jobs = {}
        else:
            self._jobs = {}
        return self._jobs

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._load_unlocked(), indent=2, ensure_ascii=True), encoding="utf-8")

    def create_job(
        self,
        *,
        job_type: str,
        account_id: str,
        job_id_prefix: str = "job",
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job_id = f"{job_id_prefix}_{uuid.uuid4().hex[:12]}"
        now = utc_now()
        job: dict[str, Any] = {
            "job_id": job_id,
            "type": job_type,
            "status": "queued",
            "progress": 0,
            "account_id": account_id,
            "created_at": now,
            "updated_at": now,
            "error": None,
            "result": None,
        }
        if fields:
            job.update(fields)
        with self._lock:
            self._load_unlocked()[job_id] = job
            self._save_unlocked()
        return job

    def get_job(self, *, job_id: str, account_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            job = self._load_unlocked().get(job_id)
            if not isinstance(job, dict):
                return None
            if account_id is not None and job.get("account_id") != account_id:
                return None
            return dict(job)

    def list_jobs(self, *, account_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [
                dict(job)
                for job in self._load_unlocked().values()
                if isinstance(job, dict) and job.get("account_id") == account_id
            ]
        jobs.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return jobs[:limit]

    def set_job(
        self,
        job_id: str,
        *,
        cancellation_terminal: bool = False,
        **updates: Any,
    ) -> dict[str, Any]:
        with self._lock:
            jobs = self._load_unlocked()
            job = dict(jobs[job_id])
            if cancellation_terminal and job.get("status") == "canceled" and updates.get("status") != "canceled":
                return job
            job.update(updates)
            job["updated_at"] = utc_now()
            jobs[job_id] = job
            self._save_unlocked()
            return dict(job)

    def set_subtask(
        self,
        job_id: str,
        name: str,
        *,
        cancellation_terminal: bool = False,
        status: str | None = None,
        phase: str | None = None,
        progress: int | None = None,
        error: str | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            jobs = self._load_unlocked()
            job = dict(jobs[job_id])
            if cancellation_terminal and job.get("status") == "canceled" and status != "canceled":
                return job
            subtasks = [dict(item) for item in job.get("subtasks", []) if isinstance(item, dict)]
            for item in subtasks:
                if item.get("name") == name:
                    if status is not None:
                        item["status"] = status
                    if phase is not None:
                        item["phase"] = phase
                    if progress is not None:
                        item["progress"] = progress
                    if error is not None:
                        item["error"] = error
                    if detail is not None:
                        item["detail"] = detail
                    break
            else:
                subtasks.append({
                    "name": name,
                    "label": name.replace("_", " ").title(),
                    "status": status or "queued",
                    "phase": phase or "queued",
                    "progress": progress or 0,
                    "error": error,
                    "detail": detail,
                })
            job["subtasks"] = subtasks
            job["updated_at"] = utc_now()
            jobs[job_id] = job
            self._save_unlocked()
            return dict(job)

    def replace_job(self, job_id: str, job: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            next_job = dict(job)
            next_job["updated_at"] = utc_now()
            self._load_unlocked()[job_id] = next_job
            self._save_unlocked()
            return dict(next_job)
