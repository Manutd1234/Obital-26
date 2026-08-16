from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.app.api.common import require_account, require_account_id
from backend.app.api.rate_limit import rate_limit
from engine.quiz.account_system import AccountStore
from engine.quiz.quiz_backend import (
    QuizBuildResponse,
    QuizGradeRequest,
    QuizGradeResponse,
    QuizSubmissionAnswer,
    grade_quiz,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quiz", tags=["quiz"])

_BASE_DIR = Path(__file__).resolve().parents[3]


def _account_store() -> AccountStore:
    return AccountStore(_BASE_DIR / "engine" / "accounts" / "accounts.json")


class BuildQuizRequest(BaseModel):
    topic: str = ""
    num_questions: int = 5
    question_mode: str = "mcq"
    difficulty: str | None = None


@router.post("/build", response_model=QuizBuildResponse)
async def build_quiz_endpoint(
    request: BuildQuizRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
    _: None = Depends(rate_limit("quiz_build")),
) -> QuizBuildResponse:
    from backend.app.services.quiz_harness import generate_quiz
    return await generate_quiz(
        topic=request.topic or "",
        num_questions=request.num_questions,
        question_mode=request.question_mode,
        difficulty=request.difficulty or "medium",
    )


class GradeQuizRequest(BaseModel):
    quiz: QuizBuildResponse
    answers: list[QuizSubmissionAnswer]


@router.post("/grade", response_model=QuizGradeResponse)
def grade_quiz_endpoint(
    request: GradeQuizRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> QuizGradeResponse:
    """Grade quiz answers server-side."""
    if request.quiz.quiz_id.startswith("shop_pack_"):
        raise HTTPException(status_code=400, detail="Shop pack quizzes must be graded through the shop endpoint.")
    grade_req = QuizGradeRequest(
        quiz_id=request.quiz.quiz_id,
        answers=request.answers,
    )
    account_id, user_dek = auth
    result = grade_quiz(quiz=request.quiz, request=grade_req)

    try:
        store = _account_store()
        store.apply_quiz_result(username=account_id, result=result, user_dek=user_dek)
    except RuntimeError as exc:
        logger.warning("Failed to record quiz result for account=%s: %s", account_id, exc)

    return result
