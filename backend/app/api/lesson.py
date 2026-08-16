# Authored by Noah — https://github.com/noah-e1528170
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, UTC
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query
from typing import Annotated, Literal
from pydantic import BaseModel, Field, model_validator

from backend.app.services.usage import check_lesson_limit, record_lesson_upload
from backend.app.api.common import require_account, require_account_id, handle_user_error
from backend.app.schemas import (
    Lesson,
    LessonCheckpoint,
    LessonMaterial,
    StartLessonRequest,
    StartLessonResponse,
    LessonAttemptRequest,
    LessonAttemptResponse,
)
from backend.app.services.storage import get_lesson, list_account_lessons, list_lessons
from backend.app.services.lesson_harness import (
    LessonGenerateRequest,
    cancel_lesson_generation_job,
    create_lesson_generation_job,
    get_lesson_generation_job,
    list_lesson_generation_jobs,
)
from backend.app.services.quiz_service import grade_lesson_quiz
from engine.quiz.account_system import AccountStore
from engine.quiz.quiz_backend import QuizBuildResponse, QuizGradeResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/lessons", tags=["lesson"])

_BASE_DIR = Path(__file__).resolve().parents[3]


class LessonGenerationJobResponse(BaseModel):
    job_id: str
    type: str
    status: str
    phase: str
    progress: int
    created_at: str
    updated_at: str
    error: str | None = None
    rejection_reason: str | None = None
    result: dict | None = None
    request: dict | None = None
    subtasks: list[dict] = Field(default_factory=list)


class GradeCheckpointAnswer(BaseModel):
    question_id: str
    answer: str


class GradeCheckpointRequest(BaseModel):
    answers: list[GradeCheckpointAnswer]


class GradeCheckpointResponse(BaseModel):
    quiz_id: str
    total_questions: int
    answered_questions: int
    correct_answers: int
    score_percent: float
    passed: bool
    results: list[dict]


class SavedChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12_000)


class AddLessonMaterialRequest(BaseModel):
    kind: Literal["chat", "quiz"]
    title: str = Field(default="", max_length=120)
    messages: list[SavedChatMessage] = Field(default_factory=list, max_length=50)
    quiz: QuizBuildResponse | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "AddLessonMaterialRequest":
        if self.kind == "chat":
            if not self.messages:
                raise ValueError("At least one chat message is required.")
            if self.quiz is not None:
                raise ValueError("Chat materials cannot include a quiz payload.")
            total_chars = sum(len(message.content) for message in self.messages)
            if total_chars > 50_000:
                raise ValueError("Chat is too large to save; keep it under 50,000 characters.")
        else:
            if self.quiz is None or not self.quiz.questions:
                raise ValueError("A quiz with at least one question is required.")
            if self.messages:
                raise ValueError("Quiz materials cannot include chat messages.")
            if len(self.quiz.questions) > 20:
                raise ValueError("A saved quiz may contain at most 20 questions.")
        return self


class AddLessonMaterialResponse(BaseModel):
    lesson_id: str
    material_id: str
    kind: Literal["chat", "quiz"]
    created: bool
    total_materials: int


def _account_store() -> AccountStore:
    return AccountStore(_BASE_DIR / "engine" / "accounts" / "accounts.json")


_EMBEDDED_QUIZ_PATTERN = re.compile(
    r"===\s*Quiz:.*?\[QUIZ_DATA_END\]",
    flags=re.DOTALL | re.IGNORECASE,
)


def _chat_markdown(messages: list[SavedChatMessage]) -> str:
    sections: list[str] = []
    for message in messages:
        content = message.content.strip()
        if message.role == "assistant":
            content = _EMBEDDED_QUIZ_PATTERN.sub("", content).strip()
        if not content:
            continue
        speaker = "You" if message.role == "user" else "Obital"
        sections.append(f"### {speaker}\n\n{content}")
    if not sections:
        raise ValueError("Chat contains no study content to save.")
    return "\n\n---\n\n".join(sections)


def _build_lesson_material(request: AddLessonMaterialRequest) -> dict:
    source_payload = request.model_dump(mode="json", exclude={"title"})
    digest = hashlib.sha256(
        json.dumps(source_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()
    title = request.title.strip()
    if not title:
        title = "Saved chat" if request.kind == "chat" else "Saved quiz"
    return {
        "material_id": f"material_{digest[:16]}",
        "source_key": f"{request.kind}:{digest}",
        "kind": request.kind,
        "title": title,
        "created_at": datetime.now(UTC).isoformat(),
        "markdown": _chat_markdown(request.messages) if request.kind == "chat" else None,
        "quiz": request.quiz.model_dump(mode="json") if request.quiz is not None else None,
    }


def _default_checkpoints(topic: str) -> list[LessonCheckpoint]:
    return [
        LessonCheckpoint(title=f"{topic} fundamentals", difficulty="easy", num_questions=2, pass_threshold_percent=60.0, question_mode="mcq"),
        LessonCheckpoint(title=f"{topic} application", difficulty="medium", num_questions=3, pass_threshold_percent=70.0, question_mode="mcq"),
        LessonCheckpoint(title=f"{topic} mastery", difficulty="hard", num_questions=3, pass_threshold_percent=75.0, question_mode="mcq"),
    ]


@router.get("", response_model=list[Lesson])
def get_lessons(
    auth: tuple[str, bytes | None] = Depends(require_account),
    source: Literal["account", "curriculum"] = "account",
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[Lesson]:
    account_id, user_dek = auth
    try:
        if source == "curriculum":
            all_lessons = list_lessons(account_id=account_id, user_dek=user_dek)
        else:
            all_lessons = list_account_lessons(account_id=account_id, user_dek=user_dek)
    except RuntimeError as exc:
        raise handle_user_error(exc) from exc
    return all_lessons[offset:offset + limit]


@router.get("/resolve", response_model=Lesson)
def resolve_lesson(
    q: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> Lesson:
    account_id, user_dek = auth
    lesson = get_lesson(q, account_id=account_id, user_dek=user_dek)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return lesson


@router.post("", response_model=StartLessonResponse)
def start_lesson(
    request: StartLessonRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> StartLessonResponse:
    """Start a new lesson on a topic."""
    account_id, user_dek = auth
    topic = request.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic must not be empty")

    lesson_id = f"lesson_{re.sub(r'[^a-z0-9]+', '_', topic.lower()).strip('_')}"
    title = request.title or f"Lesson: {topic}"
    checkpoints = request.checkpoints if request.checkpoints else _default_checkpoints(topic)
    checkpoint_dicts = [cp.model_dump() for cp in checkpoints]

    store = _account_store()
    try:
        check_lesson_limit(account_id)
        state = store.start_lesson(
            username=account_id,
            lesson_id=lesson_id,
            title=title,
            topic=topic,
            checkpoints=checkpoint_dicts,
            user_dek=user_dek,
        )
    except RuntimeError as exc:
        raise handle_user_error(exc)

    record_lesson_upload(account_id, lesson=1)
    return StartLessonResponse(
        lesson_id=str(state.get("lesson_id", lesson_id)),
        title=str(state.get("title", title)),
        topic=str(state.get("topic", topic)),
        checkpoints=checkpoints,
        current_checkpoint_index=int(state.get("current_checkpoint_index", 0)),
    )


@router.post("/generate", response_model=LessonGenerationJobResponse)
def generate_lesson(
    request: LessonGenerateRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    """Start a three-part markdown lesson generation job with embedded quizzes."""
    account_id, user_dek = auth
    return create_lesson_generation_job(account_id=account_id, request=request, user_dek=user_dek)


@router.get("/jobs/{job_id}", response_model=LessonGenerationJobResponse)
def get_lesson_job(
    job_id: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    account_id, _user_dek = auth
    job = get_lesson_generation_job(job_id=job_id, account_id=account_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Lesson generation job not found")
    return job


@router.get("/jobs", response_model=list[LessonGenerationJobResponse])
def get_lesson_jobs(
    auth: tuple[str, bytes | None] = Depends(require_account),
    limit: int = Query(10, ge=1, le=50),
) -> list[dict]:
    account_id, _user_dek = auth
    return list_lesson_generation_jobs(account_id=account_id, limit=limit)


@router.post("/jobs/{job_id}/cancel", response_model=LessonGenerationJobResponse)
def cancel_lesson_job(
    job_id: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    account_id, _user_dek = auth
    job = cancel_lesson_generation_job(job_id=job_id, account_id=account_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Lesson generation job not found")
    return job


@router.post("/{lesson_id}/materials", response_model=AddLessonMaterialResponse)
def add_lesson_material(
    lesson_id: str,
    request: AddLessonMaterialRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> AddLessonMaterialResponse:
    """Save a chat transcript or generated quiz as a lesson-level study resource."""
    account_id, user_dek = auth
    try:
        material = _build_lesson_material(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = _account_store().add_lesson_material(
            username=account_id,
            lesson_id=lesson_id,
            material=material,
            user_dek=user_dek,
        )
    except RuntimeError as exc:
        raise handle_user_error(exc, not_found="not found" in str(exc).lower()) from exc

    saved = result["material"]
    return AddLessonMaterialResponse(
        lesson_id=lesson_id,
        material_id=str(saved["material_id"]),
        kind=saved["kind"],
        created=bool(result["created"]),
        total_materials=int(result["total_materials"]),
    )


@router.get("/{lesson_id}/materials/{material_id}", response_model=LessonMaterial)
def get_lesson_material(
    lesson_id: str,
    material_id: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    """Return one saved material body for an owned lesson."""
    account_id, user_dek = auth
    progress = _account_store().get_lesson_progress(username=account_id, user_dek=user_dek)
    lessons = progress.get("lessons", {}) if isinstance(progress, dict) else {}
    lesson = lessons.get(lesson_id) if isinstance(lessons, dict) else None
    if not isinstance(lesson, dict):
        raise HTTPException(status_code=404, detail="Lesson not found")
    materials = lesson.get("materials", [])
    if not isinstance(materials, list):
        raise HTTPException(status_code=404, detail="Material not found")
    for material in materials:
        if isinstance(material, dict) and str(material.get("material_id", "")) == material_id:
            return material
    raise HTTPException(status_code=404, detail="Material not found")


@router.post("/{lesson_id}/restart")
def restart_lesson(
    lesson_id: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    """Restart a lesson (reset checkpoint progress)."""
    account_id, user_dek = auth
    store = _account_store()
    try:
        state = store.restart_lesson(username=account_id, lesson_id=lesson_id, user_dek=user_dek)
    except RuntimeError as exc:
        raise handle_user_error(exc, not_found=True)
    return {"status": "restarted", "lesson_id": lesson_id, "current_checkpoint_index": int(state.get("current_checkpoint_index", 0))}


@router.post("/{lesson_id}/checkpoints/{checkpoint_index}/grade", response_model=GradeCheckpointResponse)
def grade_checkpoint(
    lesson_id: str,
    checkpoint_index: Annotated[int, PathParam(ge=0)],
    request: GradeCheckpointRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> GradeCheckpointResponse:
    """Grade answers against a lesson checkpoint's embedded quiz."""
    account_id, _user_dek = auth

    try:
        store = _account_store()
        lesson = store.get_active_lesson(username=account_id)
        if not lesson:
            raise ValueError("No active lesson.")
        lesson_id_stored = str(lesson.get("lesson_id", ""))
        if lesson_id_stored != lesson_id:
            raise ValueError("Lesson ID mismatch.")

        checkpoints = lesson.get("checkpoints", [])
        if not isinstance(checkpoints, list) or checkpoint_index < 0 or checkpoint_index >= len(checkpoints):
            raise ValueError(f"Invalid checkpoint index {checkpoint_index}.")

        checkpoint = checkpoints[checkpoint_index]
        threshold = float(checkpoint.get("pass_threshold_percent", 70.0))
    except ValueError as exc:
        raise handle_user_error(exc, not_found=True)

    try:
        grade_response = grade_lesson_quiz(
            account_id=account_id,
            lesson_id=lesson_id,
            checkpoint_index=checkpoint_index,
            answers=[{"question_id": a.question_id, "answer": a.answer} for a in request.answers],
            user_dek=_user_dek,
        )
    except ValueError as exc:
        raise handle_user_error(exc, not_found=True)

    score_percent = grade_response.score_percent
    passed = score_percent >= threshold

    try:
        store.apply_quiz_result(username=account_id, result=grade_response, user_dek=_user_dek)
        store.apply_lesson_attempt(
            username=account_id,
            lesson_id=lesson_id,
            checkpoint_index=checkpoint_index,
            quiz_id=grade_response.quiz_id,
            score_percent=score_percent,
            passed=passed,
            user_dek=_user_dek,
        )
    except RuntimeError as exc:
        logger.warning("Failed to record lesson attempt: %s", exc)

    return GradeCheckpointResponse(
        quiz_id=grade_response.quiz_id,
        total_questions=grade_response.total_questions,
        answered_questions=grade_response.answered_questions,
        correct_answers=grade_response.correct_answers,
        score_percent=grade_response.score_percent,
        passed=passed,
        results=[r.model_dump() for r in grade_response.results],
    )


@router.post("/{lesson_id}/attempt", response_model=LessonAttemptResponse)
def record_attempt(
    lesson_id: str,
    request: LessonAttemptRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> LessonAttemptResponse:
    """Record a checkpoint attempt for a lesson."""
    account_id, user_dek = auth
    store = _account_store()
    try:
        state = store.apply_lesson_attempt(
            username=account_id,
            lesson_id=lesson_id,
            checkpoint_index=request.checkpoint_index,
            quiz_id=request.quiz_id,
            score_percent=request.score_percent,
            passed=request.passed,
            user_dek=user_dek,
        )
    except RuntimeError as exc:
        raise handle_user_error(exc, not_found=True)

    return LessonAttemptResponse(
        lesson_id=lesson_id,
        current_checkpoint_index=int(state.get("current_checkpoint_index", 0)),
        completed=bool(state.get("completed", False)),
    )


@router.get("/{lesson_id}", response_model=Lesson)
def get_lesson_by_id(
    lesson_id: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> Lesson:
    account_id, user_dek = auth
    lesson = get_lesson(lesson_id, account_id=account_id, user_dek=user_dek)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return lesson


@router.delete("/{lesson_id}")
def delete_lesson(
    lesson_id: str,
    account_id: str = Depends(require_account_id),
) -> dict:
    """Move a lesson to the bin (30-day TTL)."""
    from backend.app.services.db import SessionLocal, db_enabled
    from backend.app.services.models import BinItemModel

    store = _account_store()
    try:
        state = store.delete_lesson(username=account_id, lesson_id=lesson_id)
    except RuntimeError as exc:
        raise handle_user_error(exc, not_found=True)

    if db_enabled():
        assert SessionLocal is not None
        now = datetime.now(UTC)
        with SessionLocal() as db:
            db.add(BinItemModel(
                id=uuid.uuid4().hex[:16],
                account_id=account_id,
                item_type="lesson",
                item_id=lesson_id,
                name=str(state.get("title", lesson_id)),
                metadata_json=json.dumps({"state": state}, ensure_ascii=True),
                deleted_at=now,
                expires_at=now + timedelta(days=30),
            ))
            db.commit()

    return {"status": "deleted", "lesson_id": lesson_id}
