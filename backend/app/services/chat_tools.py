# Authored by Noah — https://github.com/noah-e1528170
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.services import db as db_service
from backend.app.services.models import AccountArtifactModel
from backend.app.services.crypto_local import encrypt_bytes, require_dek
from backend.app.schemas import ChatSettingsPatch, SettingsPatchRequest
from backend.app.services import quiz_harness
from backend.app.services.settings_service import get_settings, update_settings
from backend.app.services.storage import (
    get_rewards_dashboard,
    list_account_lessons,
    list_lessons,
    list_docs,
)
from engine.pipeline.models import CurriculumQAOutput
from engine.quiz.account_system import AccountStore
from engine.quiz.quiz_backend import (
    CORRECTNESS_THRESHOLD,
    QuizBuildResponse,
    QuizGradeRequest,
    QuizQuestion,
    QuizSubmissionAnswer,
    grade_quiz,
    _grade_question_answer,
)
from engine.shared.account_paths import account_qa_output_path

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parents[3]

# Background QA generation cache: prevents blocking request threads.
# Keyed by account_id → CurriculumQAOutput. _qa_generating tracks in-flight accounts.
import threading
_qa_cache: dict[str, "CurriculumQAOutput"] = {}
_qa_generating: set[str] = set()
_qa_lock = threading.Lock()

_pending_tool_events: dict[str, list[dict]] = {}
_tool_events_lock = threading.Lock()


def pop_pending_tool_events(account_id: str) -> list[dict]:
    """Return and clear any pending tool events for the given account."""
    with _tool_events_lock:
        return _pending_tool_events.pop(account_id, [])


def _account_store() -> AccountStore:
    return AccountStore(_BASE_DIR / "engine" / "accounts" / "accounts.json")


def _rebuild_quiz(quiz_state: dict) -> QuizBuildResponse:
    """Reconstruct a QuizBuildResponse from stored quiz state."""
    return QuizBuildResponse(
        quiz_id=quiz_state.get("quiz_id", ""),
        questions=[QuizQuestion(**q) for q in quiz_state.get("questions", [])],
    )


def _extract_question_mode(text: str) -> str:
    """Extract question mode from prompt. Defaults to mcq (unlike CLI which defaults to open)."""
    lower = text.lower()
    if any(token in lower for token in {"open ended", "open-ended", "free response", "short answer"}):
        return "open"
    return "mcq"


def _save_quiz_json(account_id: str, quiz_id: str, data: dict) -> None:
    """Save quiz JSON to the account output directory, like CLI does."""
    try:
        qa_path = account_qa_output_path(base_dir=_BASE_DIR, account_id=account_id)
        output_dir = qa_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "quiz_built.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("Failed to save quiz JSON: %s", exc)


def try_handle_tool_command(
    user_text: str,
    account_id: str,
    user_dek: bytes | None = None,
) -> str | None:
    text = user_text.strip()
    if not text:
        return None

    # ── 1. Slash commands ──
    if text.startswith("/"):
        return _handle_slash(text, account_id=account_id, user_dek=user_dek)

    # ── 2. Natural language patterns ──
    lowered = text.lower()

    if _is_start_lesson_request(lowered):
        topic = _extract_lesson_topic(text)
        if topic:
            return _handle_start_lesson(topic=topic, account_id=account_id, user_dek=user_dek)

    if _is_quiz_request(lowered):
        topic = _extract_quiz_topic(text)
        if topic:
            return _handle_quiz(topic=topic, account_id=account_id, user_dek=user_dek)

    if _is_dashboard_request(lowered):
        return _handle_dashboard(account_id=account_id, user_dek=user_dek)

    if _is_lesson_request(lowered):
        return _handle_lessons(account_id=account_id, user_dek=user_dek)

    if _is_docs_request(lowered):
        return _handle_docs(account_id=account_id, user_dek=user_dek)

    if _is_help_request(lowered):
        return _handle_help()

    # ── 3. Active quiz: explicit shortcuts only — let LLM decide for everything else ──
    store = _account_store()
    active_quiz = store.get_active_quiz(username=account_id)
    if active_quiz is not None:
        created_at = active_quiz.get("created_at", "")
        if created_at:
            try:
                quiz_age = datetime.now(UTC) - datetime.fromisoformat(created_at)
                if quiz_age > timedelta(hours=2):
                    store.clear_active_quiz(username=account_id)
                    return None
            except Exception:
                pass
        # Natural language quiz commands (like CLI's _detect_command)
        if lowered in {"submit", "done", "finish", "end"}:
            result = submit_quiz_payload(account_id=account_id, user_dek=user_dek)
            if "error" in result:
                return result["error"]
            return result["result"]
        if lowered in {"review", "check", "preview"}:
            result = review_quiz_payload(account_id=account_id)
            if "error" in result:
                return result["error"]
            return result["review"]
        if lowered in {"explain", "mistakes", "errors"}:
            result = review_quiz_payload(account_id=account_id)
            if "error" in result:
                return result["error"]
            return result["review"]
        if lowered in {"exit", "quit", "stop"}:
            result = exit_quiz_payload(account_id=account_id)
            if "error" in result:
                return result["error"]
            return result["summary"]

        # No catch-all — fall through to LLM which decides via submit_answer tool
        # whether this is a quiz answer, or answers normally if it's a general question
        return None

    return None


# ── Slash command handler ──


def _handle_slash(text: str, *, account_id: str, user_dek: bytes | None) -> str:
    cmd = text.split()[0].lower()
    parts = text.split()
    subcommand = parts[1].lower() if len(parts) > 1 else ""

    if cmd == "/dashboard":
        return _handle_dashboard(account_id=account_id, user_dek=user_dek)

    if cmd in {"/lessons", "/lesson"}:
        if subcommand == "quiz":
            return _handle_lesson_quiz(account_id=account_id, user_dek=user_dek)
        if subcommand == "continue":
            index = " ".join(parts[2:]).strip() if len(parts) > 2 else None
            return _handle_continue_lesson(account_id=account_id, user_dek=user_dek, index=index)
        if subcommand in {"open", "start"} and len(parts) > 2:
            return _handle_open_lesson(parts[2], account_id=account_id, user_dek=user_dek)
        return _handle_lessons(account_id=account_id, user_dek=user_dek)

    if cmd == "/docs":
        return _handle_docs(account_id=account_id, user_dek=user_dek)

    if cmd == "/search":
        query = text.split(maxsplit=1)[1].strip() if " " in text else ""
        return _handle_doc_search(query=query, account_id=account_id, user_dek=user_dek)

    if cmd == "/quiz":
        topic = text.split(maxsplit=1)[1].strip() if " " in text else ""
        return _handle_quiz(topic=topic, account_id=account_id, user_dek=user_dek)

    if cmd == "/review":
        result = review_quiz_payload(account_id=account_id)
        if "error" in result:
            return result["error"]
        return result["review"]

    if cmd == "/explain":
        result = review_quiz_payload(account_id=account_id)
        if "error" in result:
            return result["error"]
        return result["review"]

    if cmd == "/exit":
        result = exit_quiz_payload(account_id=account_id)
        if "error" in result:
            return result["error"]
        return result["summary"]

    if cmd == "/submit":
        result = submit_quiz_payload(account_id=account_id, user_dek=user_dek)
        if "error" in result:
            return result["error"]
        return result["result"]

    if cmd == "/settings":
        return _handle_settings(text, account_id=account_id, user_dek=user_dek)

    if cmd == "/help":
        return _handle_help()

    return f"Unknown command: `{cmd}`. Type `/help` for available commands."


# ── Pattern-based intent detection (borrowed from quiz_chatbot_cli.py) basically hard coded stuff──


def _is_dashboard_request(text: str) -> bool:
    if text in {"/dashboard", "dashboard"}:
        return True
    return any(
        token in text
        for token in {
            "show my dashboard",
            "my dashboard",
            "my stats",
            "show stats",
            "my progress",
            "show progress",
            "my performance",
            "how am i doing",
            "how am i performing",
            "what are my stats",
            "what are my points",
        }
    )


def _is_lesson_request(text: str) -> bool:
    if text in {"/lessons", "lessons", "my lessons", "show lessons", "lesson library"}:
        return True
    return any(
        token in text
        for token in {
            "show my lessons",
            "what lessons",
            "list lessons",
            "lesson status",
            "my lesson status",
            "continue lesson",
            "resume lesson",
            "back to lesson",
            "keep going with lesson"
        }
    )


def _is_start_lesson_request(text: str) -> bool:
    return any(
        token in text
        for token in {
            "start lesson",
            "give me a lesson",
            "create a lesson",
            "new lesson",
            "lesson on",
            "lesson about",
            "teach me about",
            "teach me ",
        }
    )


def _is_quiz_request(text: str) -> bool:
    return any(
        token in text
        for token in {
            "quiz me",
            "quiz on",
            "quiz about",
            "test me",
            "test on",
            "test about",
            "give me a quiz",
            "start a quiz",
            "create a quiz",
            "practice quiz",
            "practice on",
        }
    )


def _is_docs_request(text: str) -> bool:
    if text in {"/docs", "docs", "my docs", "my documents", "show docs", "show documents"}:
        return True
    return any(
        token in text
        for token in {
            "show my docs",
            "show my documents",
            "what documents",
            "list documents",
            "list docs",
            "uploaded documents",
            "uploaded docs",
        }
    )


def _is_help_request(text: str) -> bool:
    return text in {"help", "/help", "what can you do", "what do you do", "what can i do", "how does this work", "commands", "/commands"}


# ── Handlers ──


def _extract_lesson_topic(text: str) -> str | None:
    """Extract topic from phrases like 'start lesson on X', 'teach me about X'."""
    patterns = [
        r"\bstart\s+lesson\s+(?:on|about)\s+(.+)$",
        r"\bgive\s+me\s+a\s+lesson\s+(?:on|about)\s+(.+)$",
        r"\bcreate\s+a?\s*lesson\s+(?:on|about)\s+(.+)$",
        r"\bnew\s+lesson\s+(?:on|about)\s+(.+)$",
        r"\blesson\s+(?:on|about)\s+(.+)$",
        r"\bteach\s+me\s+about\s+(.+)$",
        r"\bteach\s+me\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text.strip(), flags=re.IGNORECASE)
        if match:
            topic = match.group(1).strip(" .!?")
            if topic:
                return topic
    return None


def _handle_start_lesson(*, topic: str, account_id: str, user_dek: bytes | None = None) -> str:
    """Create a new lesson and return a summary."""
    try:
        payload = create_lesson_payload(topic=topic, account_id=account_id, use_harness=True, user_dek=user_dek)
    except RuntimeError as exc:
        return f"Failed to start lesson: {exc}"

    if payload.get("harness_job_id"):
        job_id = payload["harness_job_id"]
        lines = [
            f"Generating rich lesson on '{topic}' via lesson harness...",
            f"Job ID: {job_id}",
            f"Check status with: GET /lessons/jobs/{job_id}",
            f"Once complete, use `/lesson continue` to start.",
        ]
        return "\n".join(lines)

    checkpoints = payload["checkpoints"]
    cp_count = len(checkpoints)

    lines = [
        f"Started lesson '{payload['title']}' (ID: {payload['lesson_id']}).",
        f"Checkpoints: {cp_count} ({checkpoints[0]['difficulty']} → {checkpoints[-1]['difficulty']}).",
        f"Current: checkpoint 1/{cp_count} — '{checkpoints[0]['title']}'.",
    ]

    try:
        qa = _load_qa(account_id, user_dek=user_dek)
        if qa is None:
            qa = _try_generate_qa(account_id, user_dek)
        summary = _get_topic_summary(qa, topic) if qa else ""
        if summary:
            lines.append("")
            lines.append(f"**About this topic:**")
            lines.append(summary)
    except Exception:
        pass

    lines.append("")
    lines.append("Type `/lesson quiz` to take the checkpoint quiz.")
    return "\n".join(lines)


def _handle_continue_lesson(*, account_id: str, user_dek: bytes | None, index: str | None = None) -> str:
    """Resume a lesson and show its current status."""
    all_lessons = list_account_lessons(account_id=account_id, user_dek=user_dek)
    in_progress = [l for l in all_lessons if l.status in ("active", "in_progress")]
    if not in_progress:
        return "No in-progress lessons. Start one with 'start lesson on <topic>'."

    if index:
        try:
            idx = int(index) - 1
            if 0 <= idx < len(all_lessons):
                lesson = all_lessons[idx]
                if lesson.status in ("active", "in_progress"):
                    _account_store().set_active_lesson(username=account_id, lesson_id=lesson.id)
                    return _format_lesson_status(lesson, account_id=account_id)
                return f"'{lesson.title}' is not in progress (status: {lesson.status})."
        except (ValueError, IndexError):
            pass

        wanted = index.casefold().strip()
        matches = [
            lesson
            for lesson in in_progress
            if wanted in lesson.title.casefold()
            or wanted in lesson.objective.casefold()
            or wanted in lesson.id.casefold()
        ]
        if len(matches) == 1:
            lesson = matches[0]
            _account_store().set_active_lesson(username=account_id, lesson_id=lesson.id)
            return _format_lesson_status(lesson, account_id=account_id)
        if len(matches) > 1:
            lines = [f"Multiple lessons matched '{index}':"]
            for i, lesson in enumerate(matches, 1):
                lines.append(f"  {i}. {lesson.title} - checkpoint {lesson.current_checkpoint + 1}/{lesson.total_checkpoints}")
            lines.append("\nUse `/lesson continue <index>` from the full lesson list.")
            return "\n".join(lines)

    if len(in_progress) == 1:
        lesson = in_progress[0]
        _account_store().set_active_lesson(username=account_id, lesson_id=lesson.id)
        return _format_lesson_status(lesson, account_id=account_id)

    lines = ["Multiple in-progress lessons:"]
    for i, l in enumerate(in_progress, 1):
        lines.append(f"  {i}. {l.title} - checkpoint {l.current_checkpoint + 1}/{l.total_checkpoints}")
    lines.append("\nUse `/lesson continue <index>` to select one.")
    return "\n".join(lines)


def _format_lesson_status(lesson, *, account_id: str) -> str:
    """Format a single lesson's status for display."""
    current = lesson.current_checkpoint
    total = max(1, lesson.total_checkpoints)
    lines = [
        f"=== {lesson.title} ===",
        f"Status: {lesson.status}",
        f"Checkpoint: {current + 1}/{total}",
    ]
    if lesson.status == "completed":
        lines.append("This lesson is complete!")
    else:
        if lesson.checkpoints and current < len(lesson.checkpoints):
            cp = lesson.checkpoints[current]
            lines.append(f"Next: '{cp.title}' ({cp.difficulty}, {cp.num_questions} questions, pass {cp.pass_threshold_percent}%)")
        lines.append(f"\nType `/lesson quiz` to take the checkpoint quiz.")
    return "\n".join(lines)


def _handle_open_lesson(index_str: str, *, account_id: str, user_dek: bytes | None) -> str:
    """Open a lesson by index from the lessons list."""
    all_lessons = list_account_lessons(account_id=account_id, user_dek=user_dek)
    try:
        idx = int(index_str) - 1
        if 0 <= idx < len(all_lessons):
            lesson = all_lessons[idx]
            return _format_lesson_status(lesson, account_id=account_id)
        return f"Invalid index. Use 1-{len(all_lessons)}."
    except (ValueError, IndexError):
        return f"Invalid index: {index_str}"


def _handle_lesson_quiz(*, account_id: str, user_dek: bytes | None) -> str:
    """Build a quiz for the current checkpoint of the active lesson."""
    store = _account_store()
    active_lesson = store.get_active_lesson(username=account_id)
    if active_lesson is None:
        lessons = list_account_lessons(account_id=account_id, user_dek=user_dek)
        in_progress = [l for l in lessons if l.status in ("active", "in_progress")]
        if not in_progress:
            return "No active lesson. Start one with 'start lesson on <topic>'."
        return "No active lesson set. Use `/lesson continue` to resume one."

    topic = active_lesson.get("topic", "")
    if not topic:
        return "Active lesson has no topic set."

    checkpoints = active_lesson.get("checkpoints", [])
    current_idx = int(active_lesson.get("current_checkpoint_index", 0))
    if current_idx < 0:
        return "Active lesson has an invalid checkpoint index. Restart the lesson and try again."
    if current_idx >= len(checkpoints):
        return "All checkpoints completed! This lesson is done."

    checkpoint = checkpoints[current_idx]
    difficulty = checkpoint.get("difficulty", "medium")
    num_questions = checkpoint.get("num_questions", 3)
    pass_threshold = checkpoint.get("pass_threshold_percent", 70.0)
    question_mode = checkpoint.get("question_mode", "mcq")
    checkpoint_title = checkpoint.get("title", f"Checkpoint {current_idx + 1}")
    lesson_id = active_lesson.get("lesson_id", "")

    # Use embedded quiz data from lesson harness if available
    embedded_quiz = checkpoint.get("quiz")
    if isinstance(embedded_quiz, list) and embedded_quiz:
        quiz_id = hashlib.sha256(f"{lesson_id}::checkpoint_{current_idx}".encode()).hexdigest()[:16]
        logger.info(
            "quiz_generation_stream event=lesson_embedded_quiz_started account_id=%s lesson_id=%s checkpoint_index=%s topic=%r embedded_questions=%s",
            account_id,
            lesson_id,
            current_idx,
            topic,
            len(embedded_quiz),
        )
        questions = []
        for idx, q in enumerate(embedded_quiz):
            if not isinstance(q, dict) or not q.get("question"):
                continue
            try:
                questions.append(QuizQuestion(
                    question_id=str(q.get("question_id", f"{topic}_{idx + 1}")),
                    topic_id=str(q.get("topic_id", "")),
                    topic_title=str(q.get("topic_title", topic)),
                    question=str(q.get("question", "")),
                    expected_answer=str(q.get("expected_answer", "")),
                    difficulty=str(q.get("difficulty", difficulty)),
                    question_mode=str(q.get("question_mode", question_mode)),
                    options=q.get("options") if isinstance(q.get("options"), list) else None,
                    correct_option_index=q.get("correct_option_index"),
                    source_context=q.get("source_context"),
                ))
            except ValueError as exc:
                logger.warning("Skipping invalid embedded lesson quiz question: %s", exc)
        if not questions:
            return "Embedded quiz data is empty for this checkpoint."
        quiz = QuizBuildResponse(
            quiz_id=quiz_id,
            topic_filter=topic,
            questions=questions,
        )
        topic_summary = ""
    else:
        logger.warning(
            "quiz_generation_stream event=lesson_embedded_quiz_missing account_id=%s lesson_id=%s checkpoint_index=%s topic=%r",
            account_id,
            lesson_id,
            current_idx,
            topic,
        )
        return (
            "This lesson checkpoint has no embedded quiz data. "
            "Regenerate the lesson with the backend lesson harness and try again."
        )
    quiz_state = {
        "quiz_id": quiz.quiz_id,
        "topic": topic,
        "questions": [q.model_dump() for q in quiz.questions],
        "current_index": 0,
        "answers": {},
        "question_mode": question_mode,
        "created_at": datetime.now(UTC).isoformat(),
        "lesson_id": lesson_id,
        "checkpoint_index": current_idx,
        "pass_threshold": pass_threshold,
    }
    store.set_active_quiz(username=account_id, quiz_state=quiz_state)
    if not db_service.is_db_mode():
        _save_quiz_json(account_id, quiz.quiz_id, quiz.model_dump())
    logger.info(
        "quiz_generation_stream event=lesson_quiz_active_saved account_id=%s lesson_id=%s checkpoint_index=%s topic=%r quiz_id=%s questions=%s",
        account_id,
        lesson_id,
        current_idx,
        topic,
        quiz.quiz_id,
        len(quiz.questions),
    )

    lines = [f"=== {checkpoint_title} ==="]
    lines.append(f"Topic: {topic} | Difficulty: {difficulty} | Questions: {num_questions} | Pass: {pass_threshold}%")
    lines.append("")

    if topic_summary:
        lines.append(f"**About this topic:**")
        lines.append(topic_summary)
        lines.append("")

    excerpts = _get_topic_doc_excerpts(account_id, topic, user_dek)
    if excerpts:
        lines.append("**Relevant material:**")
        for excerpt in excerpts[:2]:
            lines.append(f"  {excerpt[:200]}...")
        lines.append("")

    lines.extend(_format_question(quiz.questions[0], 0, len(quiz.questions)))
    lines.append("")
    quiz_tag_data = json.dumps(quiz.model_dump(), separators=(',', ':'), ensure_ascii=False)
    lines.append(f"[QUIZ_DATA_START]{quiz_tag_data}[QUIZ_DATA_END]")
    lines.append("Type your answer (A/B/C/D or text), or use /review, /submit, /exit.")
    return "\n".join(lines)


def _extract_quiz_topic(text: str) -> str | None:
    """Extract topic from phrases like 'quiz me on X', 'test me about X'."""
    patterns = [
        r"\bquiz\s+me\s+(?:on|about)\s+(.+)$",
        r"\bgive\s+me\s+a\s+quiz\s+(?:on|about)\s+(.+)$",
        r"\bgive\s+me\s+a\s+quiz\s+(?:for|covering|over|:)\s+(.+)$",
        r"\bstart\s+a?\s*quiz\s+(?:on|about)\s+(.+)$",
        r"\bstart\s+a?\s*quiz\s+(?:for|covering|over|:)\s+(.+)$",
        r"\bcreate\s+a?\s*quiz\s+(?:on|about)\s+(.+)$",
        r"\bcreate\s+a?\s*quiz\s+(?:for|covering|over|:)\s+(.+)$",
        r"\bpractice\s+(?:quiz\s+)?(?:on|about)\s+(.+)$",
        r"\bpractice\s+(?:quiz\s+)?(?:for|covering|over|:)\s+(.+)$",
        r"\btest\s+me\s+(?:on|about)\s+(.+)$",
        r"\btest\s+me\s+(?:for|covering|over|:)\s+(.+)$",
        r"\bquiz\s*:\s*(.+)$",
        r"\bquiz\s+(?:on|about|for|covering|over|:)\s+(.+)$",
        r"\bquiz\s+me\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text.strip(), flags=re.IGNORECASE)
        if match:
            topic = match.group(1).strip(" .!?")
            if topic:
                return topic
    return None


_QA_ARTIFACT_PATH = "output/output_curriculum_qa.json"


def _load_qa(account_id: str, user_dek: bytes | None = None) -> CurriculumQAOutput | None:
    # Check in-memory cache first (from background generation)
    cached = _qa_cache.get(account_id)
    if cached is not None:
        return cached

    from backend.app.services import db as db_service

    if db_service.is_stateless_db_mode():
        qa = _load_qa_from_db(account_id, user_dek)
        if qa is not None:
            return qa
    qa_path = account_qa_output_path(base_dir=_BASE_DIR, account_id=account_id)
    if not qa_path.exists():
        return None
    try:
        raw = json.loads(qa_path.read_text(encoding="utf-8"))
        return CurriculumQAOutput.model_validate(raw)
    except Exception as exc:
        logger.warning("Failed to load QA from disk for %s: %s", account_id, exc)
        return None


def _load_qa_from_db(account_id: str, user_dek: bytes | None) -> CurriculumQAOutput | None:
    from backend.app.services import db as db_service
    from backend.app.services.models import AccountArtifactModel
    from backend.app.services.crypto_local import decrypt_bytes

    if not db_service.db_enabled():
        return None
    assert db_service.SessionLocal is not None
    with db_service.SessionLocal() as db:
        row = (
            db.query(AccountArtifactModel)
            .where(
                AccountArtifactModel.account_id == account_id,
                AccountArtifactModel.relative_path == _QA_ARTIFACT_PATH,
            )
            .first()
        )
        if row is None:
            return None
        content_bytes = bytes(row.content)
        if user_dek is not None:
            try:
                content_bytes = decrypt_bytes(user_dek, content_bytes)
            except Exception:
                logger.warning("Failed to decrypt QA artifact for %s", account_id)
                return None
        try:
            raw = json.loads(content_bytes.decode("utf-8"))
            return CurriculumQAOutput.model_validate(raw)
        except Exception as exc:
            logger.warning("Failed to parse DB QA artifact for %s: %s", account_id, exc)
            return None

def _save_qa_to_db(account_id: str, qa: CurriculumQAOutput, user_dek: bytes | None) -> None:
    if not db_service.is_stateless_db_mode():
        return

    content_bytes = json.dumps(qa.model_dump()).encode("utf-8")
    stored = encrypt_bytes(require_dek(user_dek, "QA artifact"), content_bytes)
    assert db_service.SessionLocal is not None
    with db_service.SessionLocal() as db:
        row = db.query(AccountArtifactModel).where(
            AccountArtifactModel.account_id == account_id,
            AccountArtifactModel.relative_path == _QA_ARTIFACT_PATH,
        ).first()
        digest = hashlib.sha256(content_bytes).hexdigest()
        now = datetime.now(UTC)
        if row:
            row.content = stored
            row.sha256 = digest
            row.size_bytes = len(stored)
            row.updated_at = now
        else:
            db.add(AccountArtifactModel(
                account_id=account_id,
                relative_path=_QA_ARTIFACT_PATH,
                content=stored,
                sha256=digest,
                size_bytes=len(stored),
                updated_at=now,
            ))
        db.commit()

def _sync_run_async(coro):
    """Run a coroutine from a sync context, safe whether or not an event loop is running."""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Running loop exists — execute in a dedicated thread with its own loop
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


_QA_GENERATION_TIMEOUT = 180


def _try_generate_qa(account_id: str, user_dek: bytes | None = None) -> CurriculumQAOutput | None:
    """Auto-generate QA data from uploaded documents when pipeline output doesn't exist.

    Returns cached result if available. If generation is already in progress
    for this account, returns None (non-blocking). Otherwise starts background
    generation and returns None — the caller should handle gracefully.
    """
    # Return cached result immediately
    cached = _qa_cache.get(account_id)
    if cached is not None:
        return cached

    # Skip if generation is already in progress for this account
    with _qa_lock:
        if account_id in _qa_generating:
            return None
        _qa_generating.add(account_id)

    def _bg_generate() -> None:
        try:
            from backend.app.services.storage import list_docs

            docs = list_docs(account_id=account_id, user_dek=user_dek, include_content=True)
            if not docs:
                return

            rows = []
            for doc in docs:
                for chunk in (doc.chunks or []):
                    if chunk.text.strip():
                        rows.append({"text": chunk.text})
            if not rows:
                return

            from engine.pipeline.config import load_settings
            from engine.pipeline.qa_pipeline import CurriculumQAPipeline
            from engine.shared.account_paths import account_qa_output_path

            settings = load_settings(".env")
            qa_path = account_qa_output_path(base_dir=_BASE_DIR, account_id=account_id)
            qa_path.parent.mkdir(parents=True, exist_ok=True)

            pipeline = CurriculumQAPipeline(
                api_key=settings.deepseek_api_key,
                model=settings.deepseek_model,
                base_url=settings.deepseek_base_url,
            )

            import asyncio

            async def _run() -> CurriculumQAOutput | None:
                return await asyncio.wait_for(
                    pipeline.run(rows=rows, output_path=qa_path),
                    timeout=_QA_GENERATION_TIMEOUT,
                )

            result = _sync_run_async(_run())
            if result is not None:
                from backend.app.services.db import SessionLocal
                from backend.app.services.unlock_session import resolve_dek_for_account
                dek_stale = False
                if user_dek is not None and SessionLocal is not None:
                    with SessionLocal() as db:
                        current_dek = resolve_dek_for_account(account_id, db)
                    if current_dek is None:
                        dek_stale = True
                        logger.warning("QA DEK stale for %s: account locked or session expired, skipping persist", account_id)
                if not dek_stale:
                    _save_qa_to_db(account_id, result, user_dek)
                _qa_cache[account_id] = result
                logger.info("Auto-generated QA for %s: %d topics", account_id, len(result.qa_sets))
        except asyncio.TimeoutError:
            logger.warning("Auto QA generation timed out for %s", account_id)
        except Exception as exc:
            logger.warning("Auto QA generation failed for %s: %s", account_id, exc)
        finally:
            with _qa_lock:
                _qa_generating.discard(account_id)

    thread = threading.Thread(target=_bg_generate, daemon=True, name=f"qa-gen-{account_id}")
    thread.start()
    return None


def _get_topic_summary(qa: CurriculumQAOutput | None, topic: str) -> str:
    """Get the curriculum summary for a topic from QA data."""
    if qa is None:
        return ""
    topic_lower = topic.lower()
    for t in qa.curriculum.topics:
        if topic_lower in t.title.lower() or topic_lower in t.id.lower():
            return t.summary
    return ""


def _get_topic_doc_excerpts(account_id: str, topic: str, user_dek: bytes | None = None, limit: int = 3) -> list[str]:
    """Get relevant doc excerpts for a topic using semantic search."""
    try:
        from backend.app.services.embeddings import embed_text, server_embeddings_enabled
        from backend.app.services.storage import search_chunks_by_vector
        if not server_embeddings_enabled():
            return []
        query_vector = embed_text(topic)
        results = search_chunks_by_vector(
            query_vector=query_vector,
            top_k=limit,
            threshold=0.25,
            account_id=account_id,
            user_dek=user_dek,
        )
        return [r["text"] for r in results if "text" in r]
    except Exception:
        return []


def _handle_quiz(*, topic: str, account_id: str, user_dek: bytes | None) -> str:
    """Build a quiz on a topic, store it, and show the first question."""
    if not topic:
        return "Please specify a topic. Example: `/quiz binary trees` or 'quiz me on binary trees'."

    collected_events: list[dict] = []

    def _collect_event(event: dict) -> None:
        collected_events.append(event)

    try:
        question_mode = _extract_question_mode(topic)
        quiz = _sync_run_async(quiz_harness.generate_quiz(
            topic=topic,
            num_questions=5,
            question_mode=question_mode,
            event_callback=_collect_event,
        ))
    except Exception as exc:
        return f"Failed to build quiz: {exc}"

    if collected_events:
        with _tool_events_lock:
            _pending_tool_events[account_id] = collected_events

    if quiz.build_indicator == "failed" or not quiz.questions:
        return f"Failed to build quiz: {quiz.failure_reason or 'no questions available'}."

    quiz_state = {
        "quiz_id": quiz.quiz_id,
        "topic": topic,
        "questions": [q.model_dump() for q in quiz.questions],
        "current_index": 0,
        "answers": {},
        "question_mode": question_mode,
        "created_at": datetime.now(UTC).isoformat(),
    }
    store = _account_store()
    store.set_active_quiz(username=account_id, quiz_state=quiz_state)
    if not db_service.is_db_mode():
        _save_quiz_json(account_id, quiz.quiz_id, quiz.model_dump())

    lines = [f"=== Quiz: {topic} ===", f"Quiz ID: {quiz.quiz_id}", ""]
    lines.extend(_format_question(quiz.questions[0], 0, len(quiz.questions)))
    lines.append("")
    # Tag for browser UI converter — triggers interactive MCQ rendering
    # Format: [QUIZ_DATA_START]{...compact JSON...}[QUIZ_DATA_END]
    # Browser finalizeStream() detects this tag and activates the quiz UI
    # See index.html for full data structure reference
    quiz_tag_data = json.dumps(quiz.model_dump(), separators=(',', ':'), ensure_ascii=False)
    lines.append(f"[QUIZ_DATA_START]{quiz_tag_data}[QUIZ_DATA_END]")
    lines.append("Type your answer (A/B/C/D or text), or use /review, /submit, /exit.")
    return "\n".join(lines)


def _format_question(q: dict | QuizQuestion, index: int, total: int, existing_answer: str | None = None) -> list[str]:
    """Format a single quiz question for display."""
    if isinstance(q, dict):
        question = q["question"]
        difficulty = q.get("difficulty", "")
        options = q.get("options")
    else:
        question = q.question
        difficulty = q.difficulty
        options = q.options
    lines = [f"Q{index + 1}/{total} [{difficulty}] {question}"]
    if options:
        for j, opt in enumerate(options):
            label = chr(65 + j)
            lines.append(f"  {label}) {opt}")
    if existing_answer:
        lines.append(f"Current answer: {existing_answer}")
    return lines


def submit_answer_payload(*, answer: str, account_id: str, user_dek: bytes | None = None) -> dict:
    """Grade the current quiz question, store the answer, and advance."""
    if user_dek is not None:
        try:
            from backend.app.api.common import active_user_dek
            active_user_dek.set(user_dek)
        except Exception:
            pass
    store = _account_store()
    quiz_state = store.get_active_quiz(username=account_id)
    if quiz_state is None:
        return {"error": "No active quiz. Start one with `/quiz <topic>` or ask me to quiz you on a topic."}

    questions = quiz_state.get("questions", [])
    idx = int(quiz_state.get("current_index", 0))
    answers = quiz_state.get("answers", {})

    if idx >= len(questions):
        store.clear_active_quiz(username=account_id)
        return {"error": "Quiz already completed. Start a new one with `/quiz <topic>`."}

    q_data = questions[idx]
    q = QuizQuestion(**q_data)
    q_id = q.question_id

    score = _grade_question_answer(q=q, submitted=answer)
    is_correct = score >= CORRECTNESS_THRESHOLD
    answers[q_id] = {"answer": answer, "correct": is_correct, "score": score}

    total = len(questions)
    is_last = idx >= total - 1

    result = {
        "correct": is_correct,
        "question_index": idx,
        "question": q.question,
        "your_answer": answer,
        "expected_answer": q.expected_answer,
    }

    if hasattr(q, "explanation") and q.explanation:
        result["explanation"] = q.explanation
    elif hasattr(q, "source_context") and q.source_context:
        result["explanation"] = q.source_context

    if is_last:
        lesson_id = quiz_state.get("lesson_id")
        checkpoint_index = quiz_state.get("checkpoint_index")
        pass_threshold = quiz_state.get("pass_threshold", 70.0)

        store.clear_active_quiz(username=account_id)

        try:
            quiz = _rebuild_quiz(quiz_state)
            grade_req = QuizGradeRequest(
                quiz_id=quiz.quiz_id,
                answers=[
                    QuizSubmissionAnswer(question_id=qid, answer=a["answer"])
                    for qid, a in answers.items()
                ],
            )
            grade_result = grade_quiz(quiz=quiz, request=grade_req)
            store.apply_quiz_result(username=account_id, result=grade_result, user_dek=user_dek)
            result["finished"] = True
            result["total_questions"] = grade_result.total_questions
            result["correct_answers"] = grade_result.correct_answers
            result["score_percent"] = grade_result.score_percent
            result["summary"] = f"{grade_result.correct_answers}/{grade_result.total_questions} correct ({grade_result.score_percent}%)"

            if lesson_id is not None and checkpoint_index is not None:
                passed = grade_result.score_percent >= pass_threshold
                try:
                    store.apply_lesson_attempt(
                        username=account_id,
                        lesson_id=str(lesson_id),
                        checkpoint_index=int(checkpoint_index),
                        quiz_id=quiz.quiz_id,
                        score_percent=grade_result.score_percent,
                        passed=passed,
                        user_dek=user_dek,
                    )
                    if passed:
                        result["lesson_advance"] = f"Checkpoint {int(checkpoint_index) + 1} passed! Lesson advanced."
                    else:
                        result["lesson_advance"] = f"Score {grade_result.score_percent}% < {pass_threshold}% threshold. Retry with `/lesson quiz`."
                except Exception as exc:
                    logger.warning("Failed to apply lesson attempt: %s", exc)
        except Exception as exc:
            logger.warning("Failed to record quiz result: %s", exc)
            result["finished"] = True
            result["summary"] = "Quiz completed but failed to record result."
    else:
        quiz_state["current_index"] = idx + 1
        quiz_state["answers"] = answers
        store.set_active_quiz(username=account_id, quiz_state=quiz_state)
        next_q = questions[idx + 1]
        next_q_obj = QuizQuestion(**next_q) if isinstance(next_q, dict) else next_q
        existing = answers.get(next_q_obj.question_id)
        result["next_question"] = _format_question(
            next_q, idx + 1, total, existing_answer=existing["answer"] if existing else None
        )

    return result


def review_quiz_payload(*, account_id: str) -> dict:
    """Show current quiz progress and answers so far, re-graded like CLI's _review."""
    store = _account_store()
    quiz_state = store.get_active_quiz(username=account_id)
    if quiz_state is None:
        return {"error": "No active quiz."}

    answers = quiz_state.get("answers", {})
    idx = int(quiz_state.get("current_index", 0))
    topic = quiz_state.get("topic", "")
    quiz = _rebuild_quiz(quiz_state)

    lines = [f"=== Quiz Review: {topic} ===", ""]

    if not answers:
        lines.append("No answers yet.")
    else:
        try:
            req = QuizGradeRequest(
                quiz_id=quiz.quiz_id,
                answers=[
                    QuizSubmissionAnswer(question_id=qid, answer=a["answer"])
                    for qid, a in answers.items()
                ],
            )
            preview = grade_quiz(quiz=quiz, request=req)
            graded_by_id = {r.question_id: r for r in preview.results}
        except Exception:
            graded_by_id = {}

        for i, q in enumerate(quiz.questions):
            submitted = answers.get(q.question_id)
            if submitted is None:
                lines.append(f"Q{i + 1}/{len(quiz.questions)}: {q.question}")
                lines.append("  (not answered yet)")
                lines.append("")
                continue
            graded = graded_by_id.get(q.question_id)
            status = "likely correct" if graded and graded.is_correct else "needs work"
            lines.append(f"Q{i + 1}/{len(quiz.questions)}: {q.question} — {status} (score {graded.score if graded else '?'})")
            lines.append(f"  Your answer: {submitted['answer']}")
            if q.question_mode == "mcq" and q.options and q.correct_option_index is not None:
                label = chr(ord("A") + q.correct_option_index)
                lines.append(f"  Expected: {label}. {q.options[q.correct_option_index]}")
            elif graded:
                lines.append(f"  Expected: {graded.expected_answer}")
            lines.append("")

    lines.append(f"Answered: {len(answers)}/{len(quiz.questions)} | Current: Q{idx + 1}")
    return {"review": "\n".join(lines), "answered": len(answers), "total": len(quiz.questions)}


def exit_quiz_payload(*, account_id: str) -> dict:
    """End the current quiz early and show partial results."""
    store = _account_store()
    quiz_state = store.get_active_quiz(username=account_id)
    if quiz_state is None:
        return {"error": "No active quiz to exit."}

    questions = quiz_state.get("questions", [])
    answers = quiz_state.get("answers", {})
    topic = quiz_state.get("topic", "")

    correct = sum(1 for a in answers.values() if a.get("correct"))
    total = len(questions)
    answered = len(answers)
    score_percent = round(correct / answered * 100, 1) if answered else 0.0

    store.clear_active_quiz(username=account_id)

    return {
        "exited": True,
        "topic": topic,
        "total_questions": total,
        "answered_questions": answered,
        "correct_answers": correct,
        "score_percent": score_percent,
        "summary": f"Quiz ended early. {correct}/{answered} correct ({score_percent}%)" if answered else "Quiz ended. No questions answered.",
    }


def submit_quiz_payload(*, account_id: str, user_dek: bytes | None = None) -> dict:
    """Finalize and grade the quiz, like CLI's _grade function."""
    if user_dek is not None:
        try:
            from backend.app.api.common import active_user_dek
            active_user_dek.set(user_dek)
        except Exception:
            pass
    store = _account_store()
    quiz_state = store.get_active_quiz(username=account_id)
    if quiz_state is None:
        return {"error": "No active quiz."}

    answers = quiz_state.get("answers", {})
    if not answers:
        return {"error": "No answers submitted yet."}

    quiz = _rebuild_quiz(quiz_state)
    req = QuizGradeRequest(
        quiz_id=quiz.quiz_id,
        answers=[
            QuizSubmissionAnswer(question_id=qid, answer=a["answer"])
            for qid, a in answers.items()
        ],
    )
    result = grade_quiz(quiz=quiz, request=req)

    store.apply_quiz_result(username=account_id, result=result, user_dek=user_dek)
    store.clear_active_quiz(username=account_id)

    lines = [f"=== Result ===", f"Score: {result.correct_answers}/{result.total_questions} ({result.score_percent}%)"]
    for item in result.results:
        status = "OK" if item.is_correct else "NO"
        lines.append(f"- {item.question_id}: {status} ({item.score})")
    return {
        "result": "\n".join(lines),
        "quiz_id": result.quiz_id,
        "total_questions": result.total_questions,
        "correct_answers": result.correct_answers,
        "score_percent": result.score_percent,
    }


def _handle_dashboard(*, account_id: str, user_dek: bytes | None = None) -> str:
    data = get_rewards_dashboard(account_id=account_id, limit=10, user_dek=user_dek)
    summary = data.get("summary", {})
    trend = data.get("trend", [])
    lines = [
        "=== Dashboard ===",
        f"Points: {summary.get('total_points', 0)}",
        f"Quizzes: {summary.get('total_quizzes', 0)}",
        f"Accuracy: {summary.get('accuracy_percent', 0.0)}%",
        f"Answered: {summary.get('total_correct', 0)}/{summary.get('total_answered', 0)}",
    ]
    if trend:
        lines.append("\nRecent scores:")
        for item in trend[:5]:
            score = float(item.get("score_percent", 0.0))
            bars = "#" * max(0, min(20, int(round(score / 5.0))))
            lines.append(f"  {item.get('quiz_id', '')}: {score:.1f}% [{bars}] (+{int(item.get('points_earned', 0))} pts)")
    else:
        lines.append("\nNo quiz history yet.")
    return "\n".join(lines)


def _handle_lessons(*, account_id: str, user_dek: bytes | None = None) -> str:
    all_lessons = list_account_lessons(account_id=account_id, user_dek=user_dek)
    if not all_lessons:
        return "No lessons found. Ask me to start a lesson on a topic."
    lines = ["=== Lessons ==="]
    for i, l in enumerate(all_lessons, 1):
        status = l.status
        current = l.current_checkpoint
        total = max(1, l.total_checkpoints)
        lines.append(f"{i}. [{status}] {l.title} - checkpoint {min(current + 1, total)}/{total}")
    lines.append("\nAsk me to 'start lesson on <topic>' to create a new lesson.")
    return "\n".join(lines)


def _handle_docs(*, account_id: str, user_dek: bytes | None) -> str:
    docs = list_docs(account_id=account_id, user_dek=user_dek, include_content=False)
    if not docs:
        return "No documents uploaded yet. Upload documents via the API to get started."
    lines = ["=== Documents ==="]
    for i, d in enumerate(docs, 1):
        lines.append(f"{i}. {d.name}")
    lines.append(f"\n{len(docs)} document(s) total.")
    return "\n".join(lines)


def _handle_doc_search(*, query: str, account_id: str, user_dek: bytes | None) -> str:
    if not query:
        return "Usage: /search <query>"
    from backend.app.services.storage import search_docs

    docs = search_docs(query, account_id=account_id, user_dek=user_dek)
    if not docs:
        return f"No documents matched: {query}"
    lines = [f"=== Document search: {query} ==="]
    for i, doc in enumerate(docs[:10], 1):
        lines.append(f"{i}. {doc.name}")
    return "\n".join(lines)


def create_lesson_payload(*, topic: str, account_id: str, title: str | None = None, use_harness: bool = True, user_dek: bytes | None = None) -> dict[str, object]:
    topic = topic.strip()
    if not topic:
        raise RuntimeError("topic is required")

    if not use_harness:
        raise RuntimeError("Legacy lesson creation is disabled. Use the lesson harness with embedded quizzes.")

    if use_harness:
        from backend.app.services.lesson_harness import create_lesson_generation_job, LessonGenerateRequest
        request = LessonGenerateRequest(topic=topic, title=title)
        logger.info(
            "lesson_generation_stream event=create_lesson_requested account_id=%s topic=%r title=%r source=chat_tools",
            account_id,
            topic,
            title,
        )
        job = create_lesson_generation_job(account_id=account_id, request=request, user_dek=user_dek)
        logger.info(
            "lesson_generation_stream event=create_lesson_job_created account_id=%s topic=%r job_id=%s status=%s source=chat_tools",
            account_id,
            topic,
            job.get("job_id"),
            job.get("status"),
        )
        return {
            "lesson_id": None,
            "title": title or f"Lesson: {topic}",
            "topic": topic,
            "checkpoints": [],
            "current_checkpoint_index": 0,
            "harness_job_id": job.get("job_id"),
            "harness_status": job.get("status"),
        }


def _handle_help() -> str:
    return (
        "I can help you with:\n"
        "- **Dashboard**: 'show my stats' or `/dashboard`\n"
        "- **Lessons**: `/lessons` (list), `/lesson continue` (resume), `/lesson quiz` (start checkpoint quiz)\n"
        "- **Documents**: 'show my docs' or `/docs`\n"
        "- **Quiz**: `/quiz <topic>` or 'quiz me on binary trees'\n"
        "- **Settings**: `/settings get` or `/settings set system_prompt <prompt>`\n"
        "- **Chat**: Ask me anything about your study material!"
    )


def _handle_settings(text: str, *, account_id: str, user_dek: bytes | None) -> str:
    if text == "/settings" or text.startswith("/settings get"):
        current = get_settings(account_id=account_id, user_dek=user_dek).chat.system_prompt
        preview = current if len(current) <= 700 else current[:700] + "..."
        return f"Current system prompt:\n\n{preview}"

    prefix = "/settings set system_prompt "
    if text.startswith(prefix):
        prompt_text = text[len(prefix):].strip()
        if not prompt_text:
            return "Please provide a prompt value."
        updated = update_settings(
            account_id=account_id,
            patch=SettingsPatchRequest(chat=ChatSettingsPatch(system_prompt=prompt_text)),
            user_dek=user_dek,
        )
        preview = updated.chat.system_prompt
        if len(preview) > 500:
            preview = preview[:500] + "..."
        return f"System prompt updated.\n\n{preview}"

    return (
        "Unknown settings command. Use `/settings get` or "
        "`/settings set system_prompt <your prompt>`."
    )
