# Authored by Noah — https://github.com/noah-e1528170
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
import tempfile
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from backend.app.services.background_jobs import JsonJobStore, utc_now
from backend.app.services.llm_json import call_json_model, call_json_model_with_tools
from backend.app.services.quiz_harness import (
    best_source_context as quiz_best_source_context,
    checkpoint_question_mode as quiz_checkpoint_question_mode,
    find_part_quality_issues as quiz_find_part_quality_issues,
    format_source_context as quiz_format_source_context,
    normalize_quiz as quiz_normalize_quiz,
    part_quality_issue_summary as quiz_part_quality_issue_summary,
    part_quality_reasons as quiz_part_quality_reasons,
    should_regenerate_quiz_only as quiz_should_regenerate_quiz_only,
    tokens as quiz_tokens,
)
from backend.app.services.storage import DATA_DIR, list_docs
from engine.quiz.account_system import AccountStore

logger = logging.getLogger(__name__)

JobStatus = Literal["queued", "planning", "generating", "reviewing", "saving", "succeeded", "failed", "rejected", "canceled"]
SubtaskStatus = Literal["queued", "running", "retrying", "fallback", "succeeded", "failed", "canceled"]

BASE_DIR = Path(__file__).resolve().parents[3]
_JOB_DATA_DIR = Path(tempfile.gettempdir()) / "obital" if os.getenv("VERCEL") else DATA_DIR
_JOBS_PATH = _JOB_DATA_DIR / "lesson_generation_jobs.json"
_LOCK = threading.Lock()
_jobs: dict[str, dict] | None = None
_JOB_STORE = JsonJobStore(_JOBS_PATH)

_BROAD_TOPICS = {
    "everything",
    "all",
    "science",
    "math",
    "history",
    "biology",
    "chemistry",
    "physics",
    "computer science",
}

_WORKER_LABELS = ["foundations", "application", "mastery", "extension", "review"]
_MAX_PARALLEL_WORKERS = 3
_MIN_PARTS = 1
_MAX_PARTS = 5
_MAX_SEARCH_CALLS_PER_WORKER = 2

WORKER_SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the web for specific concepts, examples, or facts using Exa and Wikipedia. "
                "Use when you need more context to write a complete teaching checkpoint."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query for a specific concept or example",
                    },
                    "num_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                        "description": "Number of results to return",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_wikipedia",
            "description": (
                "Search Wikipedia for factual information, definitions, and explanations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Wikipedia search query",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def _worker_search_executor(tool_name: str, args: dict[str, Any]) -> str:
    """Execute a search tool call for lesson workers."""
    from backend.app.services.web_search import search_web_and_wikipedia, search_wikipedia as search_wiki

    try:
        if tool_name == "search_web":
            query = str(args.get("query") or "").strip()
            num = int(args.get("num_results") or 3)
            if not query:
                return json.dumps({"error": "empty query"})
            result = search_web_and_wikipedia(query=query, num_results=min(num, 5))
            return json.dumps(result, ensure_ascii=True)
        if tool_name == "search_wikipedia":
            query = str(args.get("query") or "").strip()
            if not query:
                return json.dumps({"error": "empty query"})
            result = search_wiki(query=query, num_results=3)
            return json.dumps(result, ensure_ascii=True)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"error": f"unknown tool: {tool_name}"})


class LessonGenerationCanceled(RuntimeError):
    pass


def _log_generation_event(job_id: str, event: str, **fields: Any) -> None:
    detail = " ".join(f"{key}={value!r}" for key, value in fields.items() if value is not None)
    logger.info("lesson_generation_stream job_id=%s event=%s %s", job_id, event, detail)


class LessonGenerateRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=240)
    use_online: bool = Field(
        default=False,
        description="Allow non-local context fallback such as Wikipedia snippets.",
    )
    max_context_chunks: int = Field(default=12, ge=1, le=30)


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
    job = _job_store().set_job(job_id, cancellation_terminal=True, **updates)
    if any(key in updates for key in ("status", "phase", "progress")):
        request = job.get("request") if isinstance(job.get("request"), dict) else {}
        logger.info(
            "lesson_generation_job update job_id=%s account_id=%s topic=%r status=%s phase=%s progress=%s",
            job_id,
            job.get("account_id"),
            request.get("topic"),
            job.get("status"),
            job.get("phase"),
            job.get("progress"),
        )
    return job


def _worker_label(index: int) -> str:
    return _WORKER_LABELS[index - 1] if 0 <= index - 1 < len(_WORKER_LABELS) else f"part_{index}"


def _worker_subtasks(count: int) -> list[dict[str, Any]]:
    return [
        {
            "name": f"{_worker_label(idx)}_worker",
            "label": f"Writing {_worker_label(idx).replace('_', ' ')}",
            "status": "queued",
            "phase": "queued",
            "progress": 0,
            "error": None,
        }
        for idx in range(1, count + 1)
    ]


def _initial_subtasks(part_count: int = 3) -> list[dict[str, Any]]:
    return [
        {
            "name": "planner",
            "label": "Planning lesson",
            "status": "queued",
            "phase": "queued",
            "progress": 0,
            "error": None,
        },
        *_worker_subtasks(part_count),
        {
            "name": "reviewer",
            "label": "Reviewing quiz quality",
            "status": "queued",
            "phase": "queued",
            "progress": 0,
            "error": None,
        },
        {
            "name": "saver",
            "label": "Saving lesson",
            "status": "queued",
            "phase": "queued",
            "progress": 0,
            "error": None,
        },
    ]


def _sync_worker_subtasks(job_id: str, part_count: int) -> None:
    job = _job_store().get_job(job_id=job_id)
    if not isinstance(job, dict):
        return
    existing = [dict(item) for item in job.get("subtasks", []) if isinstance(item, dict)]
    fixed_by_name = {
        item.get("name"): item
        for item in existing
        if item.get("name") in {"planner", "reviewer", "saver"}
    }
    workers_by_name = {
        item.get("name"): item
        for item in existing
        if str(item.get("name", "")).endswith("_worker")
    }
    workers = []
    for template in _worker_subtasks(part_count):
        workers.append(dict(workers_by_name.get(template["name"], template)))
    next_subtasks = [
        fixed_by_name.get("planner") or _initial_subtasks(0)[0],
        *workers,
        fixed_by_name.get("reviewer") or {"name": "reviewer", "label": "Reviewing quiz quality", "status": "queued", "phase": "queued", "progress": 0, "error": None},
        fixed_by_name.get("saver") or {"name": "saver", "label": "Saving lesson", "status": "queued", "phase": "queued", "progress": 0, "error": None},
    ]
    _set_job(job_id, subtasks=next_subtasks)


def _set_subtask(
    job_id: str,
    name: str,
    *,
    status: SubtaskStatus | None = None,
    phase: str | None = None,
    progress: int | None = None,
    error: str | None = None,
    detail: str | None = None,
) -> dict:
    job = _job_store().set_subtask(
        job_id,
        name,
        cancellation_terminal=True,
        status=status,
        phase=phase,
        progress=progress,
        error=error,
        detail=detail,
    )
    logger.info(
        "lesson_generation_subtask update job_id=%s name=%s status=%s phase=%s progress=%s error=%r",
        job_id,
        name,
        status,
        phase,
        progress,
        error,
    )
    return job


def _lesson_model_event_callback(job_id: str, subtask_name: str):
    reasoning_parts: list[str] = []
    last_detail_len = 0

    def on_event(event: dict[str, Any]) -> None:
        nonlocal last_detail_len
        event_type = str(event.get("type") or "")
        role = str(event.get("role") or "model")
        if event_type == "model_start":
            _set_subtask(
                job_id,
                subtask_name,
                phase="streaming_model",
                detail=f"{role}: model stream started.",
            )
            return
        if event_type == "reasoning_delta":
            delta = str(event.get("delta") or "")
            if not delta:
                return
            reasoning_parts.append(delta)
            detail = "".join(reasoning_parts).strip()
            if len(detail) - last_detail_len < 240 and "\n" not in delta:
                return
            last_detail_len = len(detail)
            _set_subtask(
                job_id,
                subtask_name,
                phase="streaming_reasoning",
                detail=detail[-2000:],
            )
            return
        if event_type == "tool_start":
            tool_name = str(event.get("tool_name") or "tool")
            query = str(event.get("query") or "")
            detail = f"Searching: {tool_name}"
            if query:
                detail = f"{detail}({query!r})"
            _set_subtask(
                job_id,
                subtask_name,
                phase="searching",
                detail=detail,
            )
            return
        if event_type == "tool_done":
            tool_name = str(event.get("tool_name") or "tool")
            _set_subtask(
                job_id,
                subtask_name,
                phase="search_complete",
                detail=f"Search done: {tool_name}",
            )
            return
        if event_type == "model_done":
            detail = "".join(reasoning_parts).strip()
            _set_subtask(
                job_id,
                subtask_name,
                phase="model_stream_complete",
                detail=(detail[-2000:] if detail else f"{role}: model stream complete."),
            )

    return on_event


def create_lesson_generation_job(
    *,
    account_id: str,
    request: LessonGenerateRequest,
    user_dek: bytes | None = None,
) -> dict:
    job = _job_store().create_job(
        job_type="lesson_generation",
        account_id=account_id,
        job_id_prefix="lesson_job",
        fields={
        "status": "queued",
        "phase": "queued",
        "progress": 0,
        "error": None,
        "rejection_reason": None,
        "result": None,
        "request": request.model_dump(),
        "subtasks": _initial_subtasks(),
        },
    )
    job_id = str(job["job_id"])
    logger.info(
        "lesson_generation_job queued job_id=%s account_id=%s topic=%r title=%r",
        job_id,
        account_id,
        request.topic,
        request.title,
    )
    _log_generation_event(job_id, "queued", account_id=account_id, topic=request.topic, title=request.title)
    thread = threading.Thread(
        target=_run_lesson_generation_job,
        kwargs={"job_id": job_id, "account_id": account_id, "request": request, "user_dek": user_dek},
        daemon=True,
        name=f"lesson-gen-{job_id}",
    )
    thread.start()
    return job


def get_lesson_generation_job(*, job_id: str, account_id: str) -> dict | None:
    return _job_store().get_job(job_id=job_id, account_id=account_id)


def cancel_lesson_generation_job(*, job_id: str, account_id: str) -> dict | None:
    with _LOCK:
        jobs = _load_jobs()
        job = jobs.get(job_id)
        if not isinstance(job, dict) or job.get("account_id") != account_id:
            return None
        if job.get("status") in {"succeeded", "failed", "rejected", "canceled"}:
            return job

        next_job = dict(job)
        next_job["status"] = "canceled"
        next_job["phase"] = "canceled"
        next_job["error"] = "Lesson generation was canceled by the user."
        next_job["result"] = None
        next_job["updated_at"] = utc_now()
        subtasks = []
        for item in next_job.get("subtasks", []) or []:
            if not isinstance(item, dict):
                continue
            subtask = dict(item)
            if subtask.get("status") in {"queued", "running", "retrying", "fallback"}:
                subtask["status"] = "canceled"
                subtask["phase"] = "canceled"
                subtask["error"] = "Canceled by user."
            subtasks.append(subtask)
        next_job["subtasks"] = subtasks
        next_job = _job_store().replace_job(job_id, next_job)
    logger.info("lesson_generation_job canceled job_id=%s account_id=%s", job_id, account_id)
    _log_generation_event(job_id, "canceled", account_id=account_id)
    return next_job


def list_lesson_generation_jobs(*, account_id: str, limit: int = 10) -> list[dict]:
    return _job_store().list_jobs(account_id=account_id, limit=limit)


def _raise_if_canceled(job_id: str) -> None:
    with _LOCK:
        job = _load_jobs().get(job_id)
    if isinstance(job, dict) and job.get("status") == "canceled":
        raise LessonGenerationCanceled("Lesson generation was canceled by the user.")


def _run_lesson_generation_job(
    *,
    job_id: str,
    account_id: str,
    request: LessonGenerateRequest,
    user_dek: bytes | None,
) -> None:
    try:
        logger.info("lesson_generation_job started job_id=%s account_id=%s topic=%r", job_id, account_id, request.topic)
        _log_generation_event(job_id, "started", account_id=account_id, topic=request.topic)
        result = asyncio.run(_generate_lesson(job_id=job_id, account_id=account_id, request=request, user_dek=user_dek))
        if result.get("rejected"):
            logger.warning(
                "lesson_generation_job rejected job_id=%s account_id=%s topic=%r reason=%r",
                job_id,
                account_id,
                request.topic,
                result.get("reason"),
            )
            _log_generation_event(job_id, "rejected", account_id=account_id, reason=result.get("reason"))
            _set_job(job_id, status="rejected", phase="rejected", progress=100, rejection_reason=result["reason"])
            return
        logger.info(
            "lesson_generation_job succeeded job_id=%s account_id=%s topic=%r lesson_id=%s title=%r",
            job_id,
            account_id,
            request.topic,
            result.get("lesson_id"),
            result.get("title"),
        )
        _log_generation_event(
            job_id,
            "succeeded",
            account_id=account_id,
            lesson_id=result.get("lesson_id"),
            title=result.get("title"),
        )
        _set_job(job_id, status="succeeded", phase="succeeded", progress=100, result=result)
    except LessonGenerationCanceled:
        logger.info("lesson_generation_job stopped after cancel job_id=%s account_id=%s topic=%r", job_id, account_id, request.topic)
        _log_generation_event(job_id, "stopped_after_cancel", account_id=account_id, topic=request.topic)
        cancel_lesson_generation_job(job_id=job_id, account_id=account_id)
    except Exception as exc:
        logger.exception(
            "lesson_generation_job failed job_id=%s account_id=%s topic=%r",
            job_id,
            account_id,
            request.topic,
        )
        _log_generation_event(job_id, "failed", account_id=account_id, topic=request.topic, error=str(exc))
        _set_job(job_id, status="failed", phase="failed", progress=100, error=str(exc))


async def _generate_lesson(
    *,
    job_id: str,
    account_id: str,
    request: LessonGenerateRequest,
    user_dek: bytes | None,
) -> dict:
    _raise_if_canceled(job_id)
    topic = request.topic.strip()
    rejected = _preflight_rejection(topic)
    if rejected:
        return {"rejected": True, "reason": rejected}

    _set_job(job_id, status="planning", phase="planning", progress=8)
    _log_generation_event(job_id, "planning_started", account_id=account_id, topic=topic)
    _set_subtask(job_id, "planner", status="running", phase="collecting_context", progress=15)
    _raise_if_canceled(job_id)
    contexts = _collect_context(topic, account_id, user_dek, max_chunks=request.max_context_chunks)
    context_source = "local"
    _log_generation_event(job_id, "context_collected", account_id=account_id, topic=topic, chunks=len(contexts), source=context_source)
    if not contexts:
        _set_subtask(job_id, "planner", status="running", phase="searching_wikipedia", progress=35, error="No local context found; trying Wikipedia.")
        wiki_contexts = _collect_wikipedia_context(topic)
        if wiki_contexts:
            contexts = wiki_contexts
            context_source = "wikipedia"
        else:
            contexts = _short_primer_context(topic)
            context_source = "short_primer"
        _log_generation_event(job_id, "context_fallback", account_id=account_id, topic=topic, chunks=len(contexts), source=context_source, reason="no_local_context")
    elif not _topic_overlaps_context(topic, contexts):
        _set_subtask(job_id, "planner", status="running", phase="searching_wikipedia", progress=35, error="Local context was weak; merging with Wikipedia.")
        doc_tokens = _doc_tokens_from_contexts(contexts)
        wiki_contexts = _collect_wikipedia_context(topic, doc_tokens=doc_tokens)
        if wiki_contexts:
            contexts = contexts + wiki_contexts
            context_source = "local+wikipedia"
        else:
            context_source = "local_weak_match"
        _log_generation_event(job_id, "context_fallback", account_id=account_id, topic=topic, chunks=len(contexts), source=context_source, reason="weak_local_context")
    if not contexts:
        contexts = _short_primer_context(topic)
        context_source = "short_primer"
        _log_generation_event(job_id, "context_fallback", account_id=account_id, topic=topic, chunks=len(contexts), source=context_source, reason="wiki_unavailable")

    _set_subtask(job_id, "planner", status="running", phase="refining_context", progress=42)
    _raise_if_canceled(job_id)
    original_count = len(contexts)
    contexts = await _refine_context(job_id=job_id, topic=topic, contexts=contexts, context_source=context_source)
    _log_generation_event(job_id, "context_refined", account_id=account_id, original=original_count, kept=len(contexts))

    _set_subtask(job_id, "planner", status="running", phase="planning_checkpoints", progress=55)
    _raise_if_canceled(job_id)
    planner = await _call_json_model(
        job_id=job_id,
        subtask_name="planner",
        role="curriculum_planner",
        payload={
            "topic": topic,
            "title": request.title or f"Lesson: {topic}",
            "context": contexts,
            "task": (
                "Plan 1 to 5 distinct lesson parts. Use 1 part for narrow facts or small uploaded notes, "
                "2 parts for compact topics, 3 parts for normal topics, and 4 or 5 only when the context clearly "
                "supports more depth. Do not force a three-part lesson. "
                "Each part must have a different teaching job, teaching focus, example type, and quiz target. "
                "Avoid repeating the same definition-first structure across parts. "
                "Make level differences meaningful: easy means identify and explain, medium means apply in a worked "
                "case, hard means analyze tradeoffs, edge cases, or transfer to a new case. "
                "Return each part with title, difficulty, cognitive_level, goal, focus, must_cover, avoid_repeating, and example_type. "
                f"Context source: {context_source}. If source is wikipedia or short_primer, keep the lesson shorter "
                "and avoid niche details that are not supported."
            ),
        },
        fallback=_fallback_plan(topic, request.title),
    )
    _raise_if_canceled(job_id)
    parts = _normalize_plan(topic, request.title, planner)
    _sync_worker_subtasks(job_id, len(parts))
    _log_generation_event(job_id, "planner_done", account_id=account_id, parts=len(parts))
    _set_subtask(job_id, "planner", status="succeeded", phase="planned", progress=100, error="")

    _set_job(job_id, status="generating", phase="generating_parallel_parts", progress=20)
    _log_generation_event(job_id, "parallel_workers_started", account_id=account_id, workers=len(parts))
    _raise_if_canceled(job_id)
    semaphore = asyncio.Semaphore(_MAX_PARALLEL_WORKERS)
    generated_parts = await asyncio.gather(
        *[
            _generate_part_worker(
                job_id=job_id,
                topic=topic,
                part=part,
                all_parts=parts,
                contexts=contexts,
                context_source=context_source,
                index=idx,
                semaphore=semaphore,
            )
            for idx, part in enumerate(parts, 1)
        ]
    )
    _raise_if_canceled(job_id)

    _set_job(job_id, status="reviewing", phase="reviewing", progress=78)
    _log_generation_event(job_id, "review_started", account_id=account_id)
    _set_subtask(job_id, "reviewer", status="running", phase="checking_parts", progress=30)
    _raise_if_canceled(job_id)
    weak_indexes = quiz_find_part_quality_issues(generated_parts)
    regenerated_indexes = list(weak_indexes)
    if weak_indexes:
        quiz_only_indexes = [
            idx for idx in weak_indexes
            if quiz_should_regenerate_quiz_only(generated_parts[idx])
        ]
        full_regeneration_indexes = [
            idx for idx in weak_indexes
            if idx not in set(quiz_only_indexes)
        ]
        _log_generation_event(
            job_id,
            "review_requested_regeneration",
            account_id=account_id,
            checkpoints=[i + 1 for i in weak_indexes],
            quiz_only=[i + 1 for i in quiz_only_indexes],
            full=[i + 1 for i in full_regeneration_indexes],
        )
        _set_subtask(
            job_id,
            "reviewer",
            status="running",
            phase="regenerating_weak_parts",
            progress=55,
            error=f"Regenerating checkpoint(s): {', '.join(str(i + 1) for i in weak_indexes)}",
        )
        regenerated_full = await asyncio.gather(
            *(
                _generate_part_worker(
                    job_id=job_id,
                    topic=topic,
                    part=parts[idx],
                    all_parts=parts,
                    contexts=contexts,
                    context_source=context_source,
                    index=idx + 1,
                    semaphore=semaphore,
                    review_retry=True,
                )
                for idx in full_regeneration_indexes
            )
        )
        regenerated_quizzes = await asyncio.gather(
            *(
                _regenerate_part_quiz(
                    job_id=job_id,
                    topic=topic,
                    part=parts[idx],
                    current_part=generated_parts[idx],
                    all_parts=parts,
                    contexts=contexts,
                    context_source=context_source,
                    index=idx + 1,
                    semaphore=semaphore,
                )
                for idx in quiz_only_indexes
            )
        )
        for part_idx, part_data in zip(full_regeneration_indexes, regenerated_full, strict=False):
            generated_parts[part_idx] = part_data
        for part_idx, part_data in zip(quiz_only_indexes, regenerated_quizzes, strict=False):
            generated_parts[part_idx] = part_data
    _raise_if_canceled(job_id)

    review = await _call_json_model(
        job_id=job_id,
        subtask_name="reviewer",
        role="lesson_reviewer",
        payload={
            "topic": topic,
            "parts": generated_parts,
            "context": contexts,
            "context_source": context_source,
            "task": (
                "Review for grounding, quality, duplicate quizzes, quiz support, and far-fetched content. "
                "Return approved=false only if the whole lesson is unsafe. "
                "For wikipedia or short_primer context, prefer concise conservative edits over rejection."
            ),
        },
        fallback={"approved": True, "issues": [], "rejection_reason": ""},
    )
    _raise_if_canceled(job_id)
    if not bool(review.get("approved", True)):
        _set_subtask(job_id, "reviewer", status="failed", phase="rejected", progress=100, error=str(review.get("rejection_reason") or "Reviewer rejected the generated lesson."))
        return {"rejected": True, "reason": str(review.get("rejection_reason") or "Reviewer rejected the generated lesson.")}

    remaining_weak_indexes = quiz_find_part_quality_issues(generated_parts)
    fallback_indexes = [idx for idx, part in enumerate(generated_parts) if bool(part.get("fallback_used"))]
    non_fallback_weak = [idx for idx in remaining_weak_indexes if idx not in fallback_indexes]

    fix_round = 0
    while non_fallback_weak and fix_round < 2:
        fix_round += 1
        _log_generation_event(job_id, "fixer_started", account_id=account_id, round=fix_round, checkpoints=[i + 1 for i in non_fallback_weak])
        _set_subtask(
            job_id,
            "reviewer",
            status="running",
            phase=f"fixing_round_{fix_round}",
            progress=80,
            error=f"Fixing checkpoint(s): {', '.join(str(i + 1) for i in non_fallback_weak)}",
        )

        fixed_parts = await asyncio.gather(
            *[
                _fix_part_quality(
                    job_id=job_id,
                    topic=topic,
                    part_plan=parts[idx],
                    current_part=generated_parts[idx],
                    contexts=contexts,
                    context_source=context_source,
                    index=idx + 1,
                )
                for idx in non_fallback_weak
            ]
        )
        for part_idx, part_data in zip(non_fallback_weak, fixed_parts, strict=False):
            generated_parts[part_idx] = part_data

        _log_generation_event(job_id, "fixer_review_started", account_id=account_id, round=fix_round)
        review = await _call_json_model(
            job_id=job_id,
            subtask_name="reviewer",
            role="lesson_reviewer",
            payload={
                "topic": topic,
                "parts": generated_parts,
                "context": contexts,
                "context_source": context_source,
                "task": (
                    "Review for grounding, quality, duplicate quizzes, quiz support, and far-fetched content. "
                    "Return approved=false only if the whole lesson is unsafe. "
                    "For wikipedia or short_primer context, prefer concise conservative edits over rejection."
                ),
            },
            fallback={"approved": True, "issues": [], "rejection_reason": ""},
        )
        _raise_if_canceled(job_id)
        if not bool(review.get("approved", True)):
            _set_subtask(job_id, "reviewer", status="failed", phase="rejected", progress=100, error=str(review.get("rejection_reason") or "Reviewer rejected the generated lesson."))
            return {"rejected": True, "reason": str(review.get("rejection_reason") or "Reviewer rejected the generated lesson.")}

        remaining_weak_indexes = quiz_find_part_quality_issues(generated_parts)
        fallback_indexes = [idx for idx, part in enumerate(generated_parts) if bool(part.get("fallback_used"))]
        non_fallback_weak = [idx for idx in remaining_weak_indexes if idx not in fallback_indexes]

    if non_fallback_weak:
        bad_indexes = sorted(set(remaining_weak_indexes))
        issue_detail = quiz_part_quality_issue_summary(generated_parts, bad_indexes)
        reason = (
            "Lesson generation did not produce usable model-written checkpoint content "
            f"for checkpoint(s): {', '.join(str(idx + 1) for idx in bad_indexes)}. "
            f"No fallback lesson was saved. Details: {issue_detail}"
        )
        _set_subtask(job_id, "reviewer", status="failed", phase="quality_rejected", progress=100, error=reason)
        return {"rejected": True, "reason": reason}

    if fallback_indexes:
        _log_generation_event(
            job_id,
            "fallback_parts_kept",
            account_id=account_id,
            checkpoints=[i + 1 for i in fallback_indexes],
            message="Reviewer approved; fallback content accepted due to lack of other quality issues.",
        )

    reviewer_issues = review.get("issues", []) if isinstance(review.get("issues"), list) else []
    _log_generation_event(job_id, "review_approved", account_id=account_id, issues=len(reviewer_issues))
    _set_subtask(job_id, "reviewer", status="succeeded", phase="approved", progress=100, error="")

    _set_job(job_id, status="saving", phase="saving", progress=90)
    _log_generation_event(job_id, "saving_started", account_id=account_id)
    _set_subtask(job_id, "saver", status="running", phase="saving", progress=50)
    _raise_if_canceled(job_id)
    saved = _save_generated_lesson(
        account_id=account_id,
        topic=topic,
        title=request.title or str(planner.get("title") or f"Lesson: {topic}"),
        parts=generated_parts,
        context_source=context_source,
        user_dek=user_dek,
    )
    _set_subtask(job_id, "saver", status="succeeded", phase="saved", progress=100, error="")
    _log_generation_event(job_id, "saved", account_id=account_id, lesson_id=saved["lesson_id"], title=saved["title"])
    return {
        "rejected": False,
        "lesson_id": saved["lesson_id"],
        "title": saved["title"],
        "topic": topic,
        "parts": generated_parts,
        "review": review,
        "quality": {
            "context_source": context_source,
            "context_chunks_original": original_count,
            "context_chunks_kept": len(contexts),
            "regenerated_checkpoints": [idx + 1 for idx in regenerated_indexes],
            "reviewer_issues": review.get("issues", []) if isinstance(review.get("issues"), list) else [],
            "fallback_used": any(bool(part.get("fallback_used")) for part in generated_parts),
        },
    }


async def _generate_part_worker(
    *,
    job_id: str,
    topic: str,
    part: dict[str, str],
    all_parts: list[dict[str, str]],
    contexts: list[dict[str, str]],
    context_source: str,
    index: int,
    semaphore: asyncio.Semaphore,
    review_retry: bool = False,
) -> dict[str, Any]:
    worker_label = _worker_label(index)
    worker_name = f"{worker_label}_worker"
    async with semaphore:
        _raise_if_canceled(job_id)
        start_phase = "review_retry" if review_retry else "generating"
        _log_generation_event(job_id, "worker_started", worker=worker_name, checkpoint=index, phase=start_phase)
        _set_subtask(job_id, worker_name, status="running", phase=start_phase, progress=15)
        for attempt in range(2):
            try:
                _raise_if_canceled(job_id)
                _log_generation_event(job_id, "worker_model_call", worker=worker_name, checkpoint=index, attempt=attempt + 1)
                _set_subtask(
                    job_id,
                    worker_name,
                    status="running" if attempt == 0 else "retrying",
                    phase="calling_model" if attempt == 0 else "retrying_model",
                    progress=35 if attempt == 0 else 55,
                )
                generated = await _call_json_model_with_tools(
                    job_id=job_id,
                    subtask_name=worker_name,
                    role=f"{worker_label}_lesson_quiz_worker",
                    tools=WORKER_SEARCH_TOOLS,
                    tool_executor=_worker_search_executor,
                    max_tool_rounds=_MAX_SEARCH_CALLS_PER_WORKER,
                    payload={
                        "topic": topic,
                        "part": part,
                        "all_parts": all_parts,
                        "part_index": index,
                        "level_contract": _level_contract(part.get("difficulty", "")),
                        "context": contexts,
                        "context_source": context_source,
                        "required_output_schema": {
                            "title": "string",
                            "difficulty": "easy|medium|hard",
                            "markdown": "complete markdown checkpoint, minimum about 900 characters. Place <quiz></quiz> on its own line where the quiz should appear — do NOT write quiz questions inside the markdown.",
                            "quiz": [
                                {
                                    "question": "string",
                                    "options": ["string", "string", "string", "string"],
                                    "correct_option_index": "integer 0-3",
                                    "expected_answer": "string",
                                    "explanation": "string",
                                }
                            ],
                            "sources": ["string"],
                        },
                        "task": (
                            "Write a complete teaching checkpoint in markdown, not a placeholder. "
                            "Use only this part's focus and example_type as the center of the checkpoint. "
                            f"Level contract: {_level_contract(part.get('difficulty', ''))} "
                            "Make the structure visibly different from the other parts when appropriate: one checkpoint "
                            "may be concept-first, another worked-example-first, another mistake-first, and another "
                            "comparison-first. Do not write a generic introduction that could fit every checkpoint. "
                            "Do not repeat the same explanation, worked example, practice task, or quiz target used by the other parts. "
                            "Include: a 2-3 paragraph explanation in plain language, key terms with definitions, "
                            "a concrete example or scenario shaped by example_type, common mistakes with corrections, and a short practice task. "
                            "Place a single line containing <quiz></quiz> where the quiz belongs, but do NOT write quiz questions, "
                            "options, or answers inside the markdown. The quiz is handled separately in the quiz field. "
                            "Minimum markdown length: about 900 characters (excluding the <quiz> tag). "
                            "For wikipedia or short_primer context, stay conservative but still explain the core idea. "
                            "If the provided context is insufficient for writing a complete checkpoint, "
                            "you may use the search_web tool to find specific examples, facts, or explanations. "
                            "Search for narrow, specific concepts (e.g., 'AVL tree rotation example') rather than "
                            "broad topics. You can search at most 2 times."
                        ),
                    },
                    fallback=_fallback_part(topic, part, contexts, index),
                    raise_errors=True,
                )
                _raise_if_canceled(job_id)
                normalized = _normalize_part(topic, part, generated, contexts, index)
                normalized["fallback_used"] = bool(normalized.get("fallback_used"))
                _raise_if_canceled(job_id)
                _log_generation_event(
                    job_id,
                    "worker_done",
                    worker=worker_name,
                    checkpoint=index,
                    questions=len(normalized.get("quiz", []) if isinstance(normalized.get("quiz"), list) else []),
                    markdown_chars=len(str(normalized.get("markdown") or "")),
                )
                _set_subtask(job_id, worker_name, status="succeeded", phase="generated", progress=100, error="")
                return normalized
            except LessonGenerationCanceled:
                _log_generation_event(job_id, "worker_canceled", worker=worker_name, checkpoint=index)
                _set_subtask(job_id, worker_name, status="canceled", phase="canceled", progress=100, error="Canceled by user.")
                raise
            except Exception as exc:
                _log_generation_event(job_id, "worker_error", worker=worker_name, checkpoint=index, attempt=attempt + 1, error=str(exc))
                logger.warning(
                    "lesson_generation_subtask model call failed job_id=%s worker=%s attempt=%s error=%s",
                    job_id,
                    worker_name,
                    attempt + 1,
                    exc,
                )
                if attempt == 0:
                    _set_subtask(job_id, worker_name, status="retrying", phase="retrying_after_error", progress=55, error=str(exc))
                    continue
                _log_generation_event(job_id, "worker_fallback", worker=worker_name, checkpoint=index)
                _set_subtask(job_id, worker_name, status="fallback", phase="using_local_fallback", progress=80, error=str(exc))
                fallback = _normalize_part(topic, part, _fallback_part(topic, part, contexts, index), contexts, index)
                fallback["fallback_used"] = True
                _set_subtask(job_id, worker_name, status="succeeded", phase="fallback_generated", progress=100, error="")
                return fallback

    fallback = _normalize_part(topic, part, _fallback_part(topic, part, contexts, index), contexts, index)
    fallback["fallback_used"] = True
    _set_subtask(job_id, worker_name, status="succeeded", phase="fallback_generated", progress=100, error="")
    return fallback


async def _regenerate_part_quiz(
    *,
    job_id: str,
    topic: str,
    part: dict[str, str],
    current_part: dict[str, Any],
    all_parts: list[dict[str, str]],
    contexts: list[dict[str, str]],
    context_source: str,
    index: int,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    worker_label = _worker_label(index)
    worker_name = f"{worker_label}_worker"
    async with semaphore:
        _raise_if_canceled(job_id)
        issues = quiz_part_quality_reasons(current_part)
        _log_generation_event(job_id, "quiz_retry_started", worker=worker_name, checkpoint=index, issues=issues)
        _set_subtask(job_id, worker_name, status="retrying", phase="retrying_quiz_only", progress=65, error=", ".join(issues))
        fallback = _fallback_part(topic, part, contexts, index)
        for attempt in range(2):
            try:
                generated = await _call_json_model(
                    job_id=job_id,
                    subtask_name=worker_name,
                    role=f"{worker_label}_quiz_retry_worker",
                    payload={
                        "topic": topic,
                        "part": part,
                        "all_parts": all_parts,
                        "part_index": index,
                        "markdown": current_part.get("markdown", ""),
                        "context": contexts,
                        "context_source": context_source,
                        "quality_issues": issues,
                        "required_output_schema": {
                            "quiz": [
                                {
                                    "question": "string",
                                    "options": ["string", "string", "string", "string"],
                                    "correct_option_index": "integer 0-3",
                                    "expected_answer": "string",
                                    "explanation": "string",
                                    "source_context": "string",
                                }
                            ],
                        },
                        "task": (
                            "Regenerate only the quiz for this checkpoint. "
                            "Do not rewrite the markdown. Every expected_answer must be directly supported "
                            "by the checkpoint markdown or provided source context. Avoid duplicate questions. "
                            "Return the quiz in the quiz field only, not inside the markdown."
                        ),
                    },
                    fallback={"quiz": fallback["quiz"]},
                    raise_errors=True,
                )
                raw_quiz = generated.get("quiz") if isinstance(generated, dict) else None
                if not isinstance(raw_quiz, list) or not raw_quiz:
                    raise ValueError("Quiz retry returned no quiz items.")
                next_part = dict(current_part)
                next_part["quiz"] = quiz_normalize_quiz(
                    raw_quiz,
                    topic,
                    part,
                    contexts=contexts,
                    markdown=str(current_part.get("markdown") or ""),
                )
                next_part["fallback_used"] = bool(next_part.get("fallback_used"))
                _log_generation_event(
                    job_id,
                    "quiz_retry_done",
                    worker=worker_name,
                    checkpoint=index,
                    attempt=attempt + 1,
                    questions=len(next_part.get("quiz", [])),
                )
                _set_subtask(job_id, worker_name, status="succeeded", phase="quiz_regenerated", progress=100, error="")
                return next_part
            except LessonGenerationCanceled:
                _set_subtask(job_id, worker_name, status="canceled", phase="canceled", progress=100, error="Canceled by user.")
                raise
            except Exception as exc:
                _log_generation_event(job_id, "quiz_retry_error", worker=worker_name, checkpoint=index, attempt=attempt + 1, error=str(exc))
                if attempt == 0:
                    _set_subtask(job_id, worker_name, status="retrying", phase="retrying_quiz_only_after_error", progress=72, error=str(exc))
                    continue
                logger.warning("lesson_generation quiz retry failed job_id=%s worker=%s error=%s", job_id, worker_name, exc)

    return await _generate_part_worker(
        job_id=job_id,
        topic=topic,
        part=part,
        all_parts=all_parts,
        contexts=contexts,
        context_source=context_source,
        index=index,
        semaphore=semaphore,
        review_retry=True,
    )


async def _fix_part_quality(
    *,
    job_id: str,
    topic: str,
    part_plan: dict[str, str],
    current_part: dict[str, Any],
    contexts: list[dict[str, str]],
    context_source: str,
    index: int,
) -> dict[str, Any]:
    worker_label = _worker_label(index)
    worker_name = f"{worker_label}_fixer"
    issues = quiz_part_quality_reasons(current_part)
    issues_clean = [i for i in issues if i != "fallback_used=true"]
    if not issues_clean:
        return current_part

    _log_generation_event(job_id, "fixer_worker_started", worker=worker_name, checkpoint=index, issues=issues_clean)
    _set_subtask(job_id, worker_name, status="running", phase="fixing", progress=70, error=", ".join(issues_clean))
    try:
        fixed = await call_json_model(
            role=f"{worker_label}_fixer",
            payload={
                "topic": topic,
                "part_plan": part_plan,
                "current_content": {
                    "title": current_part.get("title"),
                    "markdown": current_part.get("markdown"),
                    "quiz": current_part.get("quiz"),
                    "sources": current_part.get("sources"),
                },
                "issues_to_fix": issues_clean,
                "context": contexts,
                "required_output_schema": {
                    "title": "string",
                    "markdown": "string (use <quiz></quiz> on its own line for quiz placement, do not write quiz questions in markdown)",
                    "quiz": [
                        {
                            "question": "string",
                            "options": ["string", "string", "string", "string"],
                            "correct_option_index": "integer 0-3",
                            "expected_answer": "string",
                            "explanation": "string",
                            "source_context": "string",
                        }
                    ],
                    "sources": ["string"],
                },
                "task": (
                    f"Fix these quality issues in this checkpoint: {', '.join(issues_clean)}. "
                    "For quiz_item_not_object: ensure every quiz item is a proper JSON object with fields. "
                    "For missing_question: add a question string to every quiz item. "
                    "For missing_expected_answer: add an expected answer string to every quiz item. "
                    "For duplicate_question: rewrite one of the duplicate questions so each is unique. "
                    "For answer_not_grounded: ensure the answer text appears in or is directly supported "
                    "by the markdown content, and include a source_context excerpt from the checkpoint "
                    "markdown that supports the answer. "
                    "For markdown_chars: expand short markdown to at least 900 characters. "
                    "For quiz_empty: generate at least 1 quiz question grounded in the markdown. "
                    "Keep the markdown content largely unchanged unless it needs minor expansion. "
                    "Do NOT write quiz questions, options, or answers inside the markdown — use <quiz></quiz> placeholder. "
                    "Return the complete corrected part as JSON."
                ),
            },
            fallback=current_part,
            raise_errors=False,
            event_callback=_lesson_model_event_callback(job_id, worker_name),
        )
        normalized = _normalize_part(topic, part_plan, fixed, contexts, index)
        if current_part.get("fallback_used"):
            normalized["fallback_used"] = True
        _log_generation_event(
            job_id,
            "fixer_worker_done",
            worker=worker_name,
            checkpoint=index,
            questions=len(normalized.get("quiz", []) if isinstance(normalized.get("quiz"), list) else []),
            markdown_chars=len(str(normalized.get("markdown") or "")),
        )
        _set_subtask(job_id, worker_name, status="succeeded", phase="fixed", progress=100, error="")
        return normalized
    except Exception as exc:
        logger.warning("lesson_generation fixer failed job_id=%s worker=%s error=%s", job_id, worker_name, exc)
        _set_subtask(job_id, worker_name, status="failed", phase="fix_failed", progress=100, error=str(exc))
        return current_part


def _preflight_rejection(topic: str) -> str | None:
    lowered = topic.lower().strip()
    if len(lowered.split()) > 16:
        return "That lesson request is too broad. Please narrow it to one topic or skill."
    if lowered in _BROAD_TOPICS:
        return "That topic is too broad for a useful three-part lesson. Please choose a narrower topic."
    if any(token in lowered for token in {"answer key", "cheat", "fake source", "fabricate"}):
        return "I cannot generate lessons or quizzes that help with cheating or fabrication."
    return None


_MIN_OVERLAP_RATIO = 0.4
_MIN_OVERLAP_TOKENS = 2


def _collect_context(topic: str, account_id: str, user_dek: bytes | None, *, max_chunks: int) -> list[dict[str, str]]:
    docs = list_docs(account_id=account_id, user_dek=user_dek)
    topic_tokens = quiz_tokens(topic)
    if not topic_tokens:
        return []
    scored: list[tuple[int, dict[str, str]]] = []
    for doc in docs:
        for chunk in doc.chunks or []:
            text = chunk.text.strip()
            if not text:
                continue
            chunk_tokens = quiz_tokens(text)
            overlap = len(topic_tokens.intersection(chunk_tokens))
            if overlap >= _MIN_OVERLAP_TOKENS:
                scored.append((overlap, {"doc": doc.name, "chunk_id": chunk.chunk_id, "text": text[:2500]}))
    scored.sort(key=lambda item: -item[0])
    return [item for _score, item in scored[:max_chunks]]


def _collect_wikipedia_context(topic: str, doc_tokens: set[str] | None = None) -> list[dict[str, str]]:
    try:
        from backend.app.services.chat_engine import _search_wikipedia

        snippets = _search_wikipedia(topic)
    except Exception as exc:
        logger.warning("lesson_generation wikipedia fallback failed topic=%r error=%s", topic, exc)
        return []
    contexts = []
    for idx, snippet in enumerate(snippets[:5], 1):
        text = str(snippet).strip()
        if not text:
            continue
        wiki_tokens = quiz_tokens(text)
        if doc_tokens and wiki_tokens.isdisjoint(doc_tokens):
            continue
        contexts.append({
            "doc": "Wikipedia",
            "chunk_id": f"wiki_{idx}",
            "text": text[:1500],
        })
        if len(contexts) >= 3:
            break
    return contexts


def _short_primer_context(topic: str) -> list[dict[str, str]]:
    canonical = _canonical_topic(topic)
    return [{
        "doc": "Obital short primer",
        "chunk_id": "short_primer_1",
        "text": (
            f"Create a useful introductory lesson on {canonical}. "
            "Explain the definition, why the idea matters, the central mechanism, one concrete example, "
            "common beginner mistakes, and a short comprehension quiz. Avoid unsupported niche claims."
        ),
    }]


def _topic_overlaps_context(topic: str, contexts: list[dict[str, str]]) -> bool:
    topic_tokens = quiz_tokens(topic)
    if not topic_tokens:
        return False
    context_tokens: set[str] = set()
    for item in contexts[:8]:
        context_tokens.update(quiz_tokens(item.get("text", "")))
    overlap = topic_tokens.intersection(context_tokens)
    min_required = max(_MIN_OVERLAP_TOKENS, int(len(topic_tokens) * _MIN_OVERLAP_RATIO))
    return len(overlap) >= min_required


async def _refine_context(
    *,
    job_id: str,
    topic: str,
    contexts: list[dict[str, str]],
    context_source: str,
) -> list[dict[str, str]]:
    """Filter context chunks through a single LLM vetting call.

    Skips the call when only 1 chunk exists. Returns the original list
    unchanged if the LLM returns bad data or raises an exception.
    """
    if len(contexts) <= 1:
        return contexts

    numbered = []
    for idx, item in enumerate(contexts):
        snippet = item.get("text", "")[:600]
        numbered.append(f"[{idx}] ({item.get('doc', '?')}): {snippet}")

    payload = {
        "topic": topic,
        "context_source": context_source,
        "chunk_count": len(contexts),
        "chunks": numbered,
        "task": (
            "You are vetting context chunks for a lesson on the given topic. "
            "Return a JSON object with key 'keep' containing a list of 0-based indices "
            "of chunks that are genuinely relevant to the topic and should be used as lesson content. "
            "Exclude chunks that are off-topic, only loosely related by a single word, "
            "or discuss a different subject despite sharing terminology. "
            "Prefer keeping local/uploaded context over Wikipedia when both are relevant. "
            "Return at least 1 index but no more than the original count."
        ),
    }

    try:
        result = await _call_json_model(
            job_id=job_id,
            subtask_name="planner",
            role="context_refiner",
            payload=payload,
            fallback={"keep": list(range(len(contexts)))},
            raise_errors=True,
        )
    except Exception as exc:
        logger.warning("lesson_generation context_refiner failed job_id=%s error=%s", job_id, exc)
        return contexts

    keep_indices = result.get("keep")
    if not isinstance(keep_indices, list) or not keep_indices:
        return contexts

    filtered = []
    for idx in keep_indices:
        try:
            i = int(idx)
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(contexts):
            filtered.append(contexts[i])

    if not filtered:
        return contexts[:1]

    return filtered


def _doc_tokens_from_contexts(contexts: list[dict[str, str]]) -> set[str]:
    result: set[str] = set()
    for item in contexts:
        result.update(quiz_tokens(item.get("text", "")))
    return result


def _canonical_topic(topic: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", topic.lower()).strip()
    aliases = {
        "dijiskra": "Dijkstra's algorithm",
        "dijskra": "Dijkstra's algorithm",
        "dijkstra": "Dijkstra's algorithm",
        "dijkstras algorithm": "Dijkstra's algorithm",
        "dijkstra algorithm": "Dijkstra's algorithm",
    }
    return aliases.get(normalized, topic.strip() or "the topic")


def _level_contract(difficulty: str) -> str:
    normalized = difficulty.strip().lower()
    if normalized == "hard":
        return "Analyze assumptions, tradeoffs, edge cases, and transfer the idea to a new or tricky case."
    if normalized == "medium":
        return "Apply the idea step by step in a concrete worked case and interpret each intermediate state."
    return "Identify the core idea, define key terms, and explain the simplest accurate mental model."


async def _call_json_model(
    *,
    job_id: str,
    subtask_name: str,
    role: str,
    payload: dict[str, Any],
    fallback: dict[str, Any],
    raise_errors: bool = False,
) -> dict[str, Any]:
    return await call_json_model(
        role=role,
        payload=payload,
        fallback=fallback,
        raise_errors=raise_errors,
        event_callback=_lesson_model_event_callback(job_id, subtask_name),
    )


async def _call_json_model_with_tools(
    *,
    job_id: str,
    subtask_name: str,
    role: str,
    payload: dict[str, Any],
    tools: list[dict[str, Any]],
    tool_executor: Callable[[str, dict[str, Any]], str],
    max_tool_rounds: int,
    fallback: dict[str, Any],
    raise_errors: bool = False,
) -> dict[str, Any]:
    return await call_json_model_with_tools(
        role=role,
        payload=payload,
        tools=tools,
        tool_executor=tool_executor,
        max_tool_rounds=max_tool_rounds,
        fallback=fallback,
        raise_errors=raise_errors,
        event_callback=_lesson_model_event_callback(job_id, subtask_name),
    )


def _fallback_plan(topic: str, title: str | None) -> dict[str, Any]:
    canonical = _canonical_topic(topic)
    if canonical == "Dijkstra's algorithm":
        parts = [
            {
                "title": "Dijkstra foundations",
                "difficulty": "easy",
                "goal": "Understand what Dijkstra's algorithm computes and why non-negative weights matter.",
                "focus": "definitions, shortest-path goal, tentative distances, visited/finalized nodes",
                "must_cover": ["source node", "tentative distance", "relaxation", "non-negative edge weights"],
                "avoid_repeating": "priority queue implementation details and advanced edge cases",
                "example_type": "tiny weighted graph traced by hand from one source",
            },
            {
                "title": "Dijkstra implementation",
                "difficulty": "medium",
                "goal": "Apply Dijkstra's algorithm with a priority queue and edge relaxation.",
                "focus": "algorithm steps, priority queue workflow, stale queue entries, adjacency lists",
                "must_cover": ["initialize distances", "pop closest node", "relax outgoing edges", "update queue"],
                "avoid_repeating": "basic definition-only explanation and negative-weight alternatives",
                "example_type": "pseudocode plus a queue trace over a small graph",
            },
            {
                "title": "Dijkstra mastery",
                "difficulty": "hard",
                "goal": "Analyze correctness assumptions, complexity, and when not to use Dijkstra's algorithm.",
                "focus": "correctness intuition, complexity tradeoffs, failure cases with negative edges",
                "must_cover": ["greedy invariant", "O((V+E) log V)", "negative edge limitation", "Bellman-Ford contrast"],
                "avoid_repeating": "basic hand trace and introductory definitions",
                "example_type": "counterexample showing why a negative edge breaks the greedy guarantee",
            },
        ]
    else:
        parts = [
            {
                "title": f"{topic} foundations",
                "difficulty": "easy",
                "goal": f"Understand the core ideas and vocabulary of {topic}.",
                "focus": "definition, purpose, core vocabulary, and simplest mental model",
                "must_cover": ["what it is", "why it matters", "core terms", "simple example"],
                "avoid_repeating": "implementation details and advanced tradeoffs",
                "example_type": "small concrete beginner example",
            },
            {
                "title": f"{topic} application",
                "difficulty": "medium",
                "goal": f"Apply {topic} to realistic examples.",
                "focus": "procedure, step-by-step application, and practical decisions",
                "must_cover": ["inputs", "steps", "intermediate state", "result interpretation"],
                "avoid_repeating": "introductory definitions and mastery-level edge cases",
                "example_type": "worked example with intermediate states",
            },
            {
                "title": f"{topic} mastery",
                "difficulty": "hard",
                "goal": f"Analyze tradeoffs and edge cases in {topic}.",
                "focus": "limitations, tradeoffs, edge cases, and comparison with alternatives",
                "must_cover": ["assumptions", "failure modes", "complexity or cost", "when to choose alternatives"],
                "avoid_repeating": "basic intro and the same worked example from earlier parts",
                "example_type": "edge-case or comparison example",
            },
        ]
    return {
        "title": title or f"Lesson: {topic}",
        "parts": parts,
    }


def _normalize_plan(topic: str, title: str | None, raw: dict[str, Any]) -> list[dict[str, str]]:
    parts = raw.get("parts")
    if not isinstance(parts, list) or not _MIN_PARTS <= len(parts) <= _MAX_PARTS:
        return _fallback_plan(topic, title)["parts"]
    labels = ["foundations", "application", "mastery", "extension", "review"]
    difficulties = ["easy", "medium", "hard", "hard", "medium"]
    out = []
    for idx in range(len(parts)):
        item = parts[idx] if isinstance(parts[idx], dict) else {}
        out.append({
            "title": str(item.get("title") or f"{topic} {labels[idx]}"),
            "difficulty": str(item.get("difficulty") or difficulties[idx]),
            "cognitive_level": str(item.get("cognitive_level") or _level_contract(str(item.get("difficulty") or difficulties[idx]))),
            "goal": str(item.get("goal") or f"Study {topic} {labels[idx]}."),
            "focus": str(item.get("focus") or item.get("teaching_focus") or _fallback_plan(topic, title)["parts"][min(idx, 2)]["focus"]),
            "must_cover": item.get("must_cover") if isinstance(item.get("must_cover"), list) else _fallback_plan(topic, title)["parts"][min(idx, 2)]["must_cover"],
            "avoid_repeating": str(item.get("avoid_repeating") or _fallback_plan(topic, title)["parts"][min(idx, 2)]["avoid_repeating"]),
            "example_type": str(item.get("example_type") or _fallback_plan(topic, title)["parts"][min(idx, 2)]["example_type"]),
        })
    return out


def _fallback_part(topic: str, part: dict[str, str], contexts: list[dict[str, str]], index: int) -> dict[str, Any]:
    evidence = contexts[(index - 1) % len(contexts)] if contexts else {"doc": "local material", "text": ""}
    source = evidence.get("doc", "local material")
    text = str(evidence.get("text", "") or "")
    canonical = _canonical_topic(topic)
    explanation = _fallback_explanation(canonical, part, index)
    example = _fallback_worked_example(canonical, index)
    mistakes = _fallback_common_mistakes(canonical)
    markdown = (
        f"# Part {index}: {part['title']}\n\n"
        f"## Goal\n{part['goal']}\n\n"
        f"## Explanation\n{explanation}\n\n"
        f"## Worked Example\n{example}\n\n"
        f"## Common Mistakes\n{mistakes}\n\n"
        "## Practice\nExplain the checkpoint idea in 4-6 sentences. Then make up a tiny example, "
        "solve it step by step, and name one condition that must be true for the method to work.\n\n"
        f"## Source Note\nThis checkpoint was generated from limited context. "
        f"Supporting context available: {text[:500] or 'short primer only'}\n"
    )
    return {
        "title": part["title"],
        "difficulty": part["difficulty"],
        "markdown": markdown,
        "quiz": [
            {
                "question": f"What is the main goal of {part['title']}?",
                "options": ["Recall isolated facts", part["goal"], "Ignore source material", "Skip examples"],
                "correct_option_index": 1,
                "expected_answer": part["goal"],
                "explanation": f"This part focuses on: {part['goal']}",
                "source_context": quiz_format_source_context(evidence),
            }
        ],
        "sources": [source],
        "fallback_used": True,
    }


def _fallback_explanation(canonical: str, part: dict[str, str], index: int) -> str:
    if canonical == "Dijkstra's algorithm":
        if index == 1:
            return (
                "Dijkstra's algorithm is a shortest-path algorithm for graphs with non-negative edge weights. "
                "It answers questions like: from this starting node, what is the cheapest known way to reach every "
                "other node? The algorithm keeps a tentative distance for each node. At the start, the source has "
                "distance 0 and every other node is treated as unknown or infinity.\n\n"
                "The central idea is greedy: repeatedly choose the unvisited node with the smallest tentative "
                "distance, then relax its outgoing edges. Relaxing an edge means checking whether going through the "
                "current node gives a cheaper route to a neighbor. Once the smallest-distance node is chosen, its "
                "distance is final because all edge weights are non-negative, so no later path can sneak in with a "
                "lower cost."
            )
        if index == 2:
            return (
                "In practice, Dijkstra's algorithm is usually implemented with a priority queue. The queue stores "
                "candidate nodes ordered by their current best distance. Each time you pop the cheapest candidate, "
                "you skip it if it is stale, otherwise you use it to improve neighboring distances.\n\n"
                "This matters because scanning every unvisited node is slow on large graphs. With an adjacency list "
                "and a priority queue, the algorithm focuses only on edges that actually exist. The result is a "
                "practical method for routing, map navigation, network costs, and any setting where every step has "
                "a non-negative price."
            )
        return (
            "The main tradeoff in Dijkstra's algorithm is correctness versus assumptions. It is reliable only when "
            "edge weights are non-negative. If an edge can have a negative cost, a node that looked final might later "
            "become cheaper, which breaks the greedy guarantee.\n\n"
            "Mastery means being able to identify the graph representation, initialize distances correctly, relax "
            "edges carefully, and explain why the smallest tentative distance can be finalized. It also means knowing "
            "when to choose a different algorithm, such as Bellman-Ford for graphs with negative edges."
        )
    return (
        f"This checkpoint introduces {canonical} through the goal: {part['goal']} Start by identifying the main "
        "problem the idea solves, then connect each definition to that problem. A useful explanation should answer "
        "what the idea is, why it is useful, what steps or rules it follows, and what assumptions must hold.\n\n"
        f"For {canonical}, do not stop at memorizing terms. Work through a small example and narrate each decision. "
        "That turns the topic from a label into a procedure you can apply."
    )


def _fallback_worked_example(canonical: str, index: int) -> str:
    if canonical == "Dijkstra's algorithm":
        return (
            "Suppose the start node is A. Edges are A-B with cost 2, A-C with cost 5, B-C with cost 1, and B-D "
            "with cost 4. Initialize distances as A=0, B=infinity, C=infinity, D=infinity. From A, relax its edges: "
            "B becomes 2 and C becomes 5. The smallest unvisited distance is B=2, so visit B next. From B, C can be "
            "improved to 3 because A->B->C costs 2+1, and D becomes 6 because A->B->D costs 2+4. Next visit C=3, "
            "then D=6. The final shortest distances from A are A=0, B=2, C=3, D=6."
        )
    return (
        f"Create a three-item example for {canonical}. Label the starting state, apply the first rule or step, "
        "write down what changed, and repeat until you reach the result. The important habit is to show the state "
        "after each step rather than jumping straight to the answer."
    )


def _fallback_common_mistakes(canonical: str) -> str:
    if canonical == "Dijkstra's algorithm":
        return (
            "- Using Dijkstra's algorithm with negative edge weights. Use Bellman-Ford instead when negatives exist.\n"
            "- Marking a node final too early. Only finalize the unvisited node with the smallest tentative distance.\n"
            "- Forgetting to relax through the current node. Always compare old_distance[neighbor] with "
            "distance[current] + edge_weight.\n"
            "- Confusing shortest number of edges with lowest total weight. Dijkstra minimizes total cost, not hop count."
        )
    return (
        f"- Memorizing the name of {canonical} without explaining the problem it solves.\n"
        "- Skipping the assumptions or conditions that make the method valid.\n"
        "- Jumping from input to answer without showing intermediate steps.\n"
        "- Treating examples as decoration instead of using them to test understanding."
    )


_QUIZ_TAG_PATTERN = re.compile(r"<quiz>.*?</quiz>", re.DOTALL | re.IGNORECASE)


def _strip_quiz_from_markdown(markdown: str) -> str:
    return _QUIZ_TAG_PATTERN.sub("", markdown).strip()


def _normalize_part(
    topic: str,
    planned: dict[str, str],
    raw: dict[str, Any],
    contexts: list[dict[str, str]],
    index: int,
) -> dict[str, Any]:
    fallback = _fallback_part(topic, planned, contexts, index)
    raw_markdown = str(
        raw.get("markdown")
        or raw.get("lesson_markdown")
        or raw.get("content")
        or raw.get("body")
        or ""
    ).strip()
    raw_quiz = raw.get("quiz") or raw.get("questions") or raw.get("checkpoint_quiz")
    raw_sources = raw.get("sources") or raw.get("source") or raw.get("references")
    used_fallback = bool(raw.get("fallback_used"))
    markdown = _strip_quiz_from_markdown(raw_markdown) or fallback["markdown"]
    if not raw_markdown:
        used_fallback = True
    quiz = raw_quiz if isinstance(raw_quiz, list) and raw_quiz else fallback["quiz"]
    if not isinstance(raw_quiz, list) or not raw_quiz:
        used_fallback = True
    if isinstance(raw_sources, str) and raw_sources.strip():
        sources = [raw_sources]
    elif isinstance(raw_sources, list) and raw_sources:
        sources = raw_sources
    else:
        sources = fallback["sources"]
    return {
        "title": str(raw.get("title") or planned["title"]),
        "difficulty": str(raw.get("difficulty") or planned["difficulty"]),
        "goal": planned["goal"],
        "markdown": markdown,
        "quiz": quiz_normalize_quiz(quiz, topic, planned, contexts=contexts, markdown=markdown),
        "sources": [str(s) for s in sources][:5],
        "context_source": raw.get("context_source"),
        "fallback_used": used_fallback,
    }


def _save_generated_lesson(
    *,
    account_id: str,
    topic: str,
    title: str,
    parts: list[dict[str, Any]],
    context_source: str,
    user_dek: bytes | None = None,
) -> dict[str, Any]:
    lesson_id = f"lesson_{re.sub(r'[^a-z0-9]+', '_', topic.lower()).strip('_')}_{uuid.uuid4().hex[:6]}"
    checkpoints = []
    thresholds = [60.0, 70.0, 75.0]
    for idx, part in enumerate(parts):
        quiz = part.get("quiz", [])
        quiz = quiz if isinstance(quiz, list) else []
        checkpoints.append({
            "title": part["title"],
            "difficulty": part["difficulty"],
            "num_questions": max(1, len(quiz)),
            "pass_threshold_percent": thresholds[min(idx, len(thresholds) - 1)],
            "question_mode": quiz_checkpoint_question_mode(quiz),
            "markdown": part["markdown"],
            "quiz": quiz,
            "sources": part.get("sources", []),
            "context_source": part.get("context_source") or context_source,
            "generation_metadata": {
                "context_source": part.get("context_source") or context_source,
                "fallback_used": bool(part.get("fallback_used")),
            },
        })
    store = AccountStore(BASE_DIR / "engine" / "accounts" / "accounts.json")
    state = store.start_lesson(
        username=account_id,
        lesson_id=lesson_id,
        title=title,
        topic=topic,
        checkpoints=checkpoints,
        user_dek=user_dek,
    )
    from backend.app.services.usage import record_lesson_upload
    record_lesson_upload(account_id, lesson=1)

    tags = _derive_lesson_tags(topic, parts)
    return {"lesson_id": lesson_id, "title": title, "state": state, "tags": tags}


def _derive_lesson_tags(topic: str, parts: list[dict[str, Any]]) -> list[str]:
    """Derive up to 5 tags from topic and part titles."""
    texts = [topic]
    for part in parts:
        texts.append(str(part.get("title", "")))
    combined = " ".join(texts).lower()
    tokens = re.findall(r"[a-z0-9]+", combined)
    stopwords = {"lesson", "on", "about", "the", "a", "an", "and", "or", "of", "in", "to", "for", "is", "with",
                 "basic", "advanced", "introduction", "fundamentals", "tutorial", "guide", "overview",
                 "part", "1", "2", "3", "4", "5"}
    tags = []
    for token in tokens:
        if token not in stopwords and len(token) > 2:
            tags.append(token)
    seen = set()
    unique = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique.append(t)
        if len(unique) >= 5:
            break
    return unique
