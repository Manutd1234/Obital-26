# Authored by Noah — https://github.com/noah-e1528170
from __future__ import annotations

import logging
from pathlib import Path

import hashlib

from engine.quiz.account_system import AccountStore
from engine.quiz.quiz_backend import (
    QuizBuildResponse,
    QuizGradeRequest,
    QuizGradeResponse,
    QuizQuestion,
    QuizSubmissionAnswer,
    grade_quiz,
)

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parents[3]
_ACCOUNTS_PATH = _BASE_DIR / "engine" / "accounts" / "accounts.json"


def _account_store() -> AccountStore:
    return AccountStore(_ACCOUNTS_PATH)


def build_checkpoint_quiz(account_id: str, user_dek: bytes | None = None) -> QuizBuildResponse:
    """Disabled legacy checkpoint quiz builder.

    Lesson checkpoints must use quizzes embedded by the lesson harness.
    Standalone ad-hoc quizzes use quiz_harness.generate_quiz().
    """
    raise RuntimeError("Legacy checkpoint quiz building is disabled. Regenerate the lesson with the lesson harness.")


def build_topic_quiz(
    account_id: str,
    topic: str,
    num_questions: int = 5,
    question_mode: str = "mcq",
    user_dek: bytes | None = None,
) -> QuizBuildResponse:
    """Deprecated: use quiz_harness.generate_quiz() instead.

    Standalone quizzes are now LLM-generated via the quiz harness pipeline.
    This QA-pool selector is retained only for backward compatibility.
    """
    raise RuntimeError(
        "build_topic_quiz() is deprecated. Use quiz_harness.generate_quiz() for LLM-generated quizzes."
    )


def grade_lesson_quiz(
    *,
    account_id: str,
    lesson_id: str,
    checkpoint_index: int,
    answers: list[dict],
    user_dek: bytes | None = None,
) -> QuizGradeResponse:
    """Grade answers against a lesson checkpoint's embedded quiz."""
    logger.info(
        "quiz_generation_stream event=lesson_grade_started account_id=%s lesson_id=%s checkpoint_index=%s answers=%s",
        account_id,
        lesson_id,
        checkpoint_index,
        len(answers),
    )
    if checkpoint_index < 0:
        raise ValueError(f"Invalid checkpoint index {checkpoint_index}.")

    store = _account_store()
    lesson = store.get_active_lesson(username=account_id)
    if not lesson:
        raise ValueError("No active lesson.")

    lesson_id_stored = str(lesson.get("lesson_id", ""))
    if lesson_id_stored != lesson_id:
        raise ValueError("Lesson ID mismatch.")

    checkpoints = lesson.get("checkpoints", [])
    if not isinstance(checkpoints, list) or checkpoint_index >= len(checkpoints):
        raise ValueError(f"Invalid checkpoint index {checkpoint_index}.")

    checkpoint = checkpoints[checkpoint_index]
    quiz_data = checkpoint.get("quiz")
    if not isinstance(quiz_data, list) or not quiz_data:
        raise ValueError("Checkpoint has no quiz data to grade.")

    topic = str(lesson.get("topic", ""))
    topic_id = hashlib.sha256(topic.encode("utf-8")).hexdigest()[:12]

    questions: list[QuizQuestion] = []
    for q in quiz_data:
        if not isinstance(q, dict) or not q.get("question"):
            continue
        try:
            questions.append(
                QuizQuestion(
                    question_id=str(q.get("question_id", "")),
                    topic_id=str(q.get("topic_id", topic_id)),
                    topic_title=str(q.get("topic_title", topic)),
                    question=str(q.get("question", "")),
                    expected_answer=str(q.get("expected_answer", "")),
                    difficulty=str(q.get("difficulty", "medium")),
                    question_mode=str(q.get("question_mode", "mcq")),
                    options=q.get("options") if isinstance(q.get("options"), list) else None,
                    correct_option_index=q.get("correct_option_index"),
                    source_context=q.get("source_context"),
                )
            )
        except ValueError as exc:
            logger.warning("Skipping invalid lesson quiz question: %s", exc)

    if not questions:
        raise ValueError("Checkpoint has no valid quiz questions to grade.")

    quiz_id = hashlib.sha256(f"{lesson_id}::checkpoint_{checkpoint_index}".encode()).hexdigest()[:16]
    quiz_response = QuizBuildResponse(
        quiz_id=quiz_id,
        topic_filter=topic,
        questions=questions,
    )

    grade_answers = []
    for a in answers:
        qid = a.get("question_id", "")
        ans = a.get("answer", "")
        if qid:
            grade_answers.append(QuizSubmissionAnswer(question_id=qid, answer=ans))
    grade_request = QuizGradeRequest(quiz_id=quiz_id, answers=grade_answers)
    result = grade_quiz(quiz=quiz_response, request=grade_request)
    logger.info(
        "quiz_generation_stream event=lesson_grade_done account_id=%s lesson_id=%s checkpoint_index=%s quiz_id=%s answered=%s score=%.2f",
        account_id,
        lesson_id,
        checkpoint_index,
        quiz_id,
        result.answered_questions,
        result.score_percent,
    )
    return result
