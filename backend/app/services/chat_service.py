from __future__ import annotations

import logging
import os
import re
import time
from datetime import UTC, datetime

from fastapi import HTTPException
from backend.app.services.usage import check_token_limit, record_token_usage, estimate_tokens
from backend.app.schemas import ChatMessage, ConfirmActionResponse, SendMessageRequest, SendMessageResponse
from backend.app.services.chat_engine import generate_reply, generate_reply_stream
from backend.app.services.chat_tools import pop_pending_tool_events, try_handle_tool_command
from backend.app.services.harness_state import pop_pending_action
from backend.app.services.lesson_harness import get_lesson_generation_job
from backend.app.services.study_tools import execute_tool
from backend.app.services.storage import (
    append_message,
    get_doc_texts_for_chunk_ids,
    get_lesson,
    list_docs,
    get_session,
)

logger = logging.getLogger(__name__)

_LESSON_JOB_RE = re.compile(r"\b(lesson_job_[a-f0-9]{12})\b")
_LESSON_JOB_POLL_INTERVAL_SEC = 1.0
_LESSON_JOB_STREAM_TIMEOUT_SEC = float(os.getenv("OBITAL_LESSON_JOB_STREAM_TIMEOUT_SEC", "120"))
_LESSON_JOB_STREAM_FOLLOW = os.getenv(
    "OBITAL_LESSON_JOB_STREAM_FOLLOW",
    "true" if os.getenv("VERCEL") else "",
).strip().lower() in {"1", "true", "yes"}
_DOC_MENTION_RE = re.compile(r'@(?:"([^"]+)"|([^\s@]+))')

def _normalize_reply_result(result: tuple[str, int] | str) -> tuple[str, int]:
    if isinstance(result, tuple):
        return result
    return result, estimate_tokens(result)


def _estimate_turn_tokens(
    *,
    user_text: str,
    assistant_text: str,
    doc_texts: list[str],
    past_messages: list[ChatMessage],
) -> int:
    parts = [user_text, assistant_text, *doc_texts]
    parts.extend(message.text for message in past_messages[-5:])
    return sum(estimate_tokens(part) for part in parts if part)


def _mentioned_doc_chunk_texts(
    *,
    text: str,
    account_id: str,
    user_dek: bytes | None,
    existing_chunk_ids: list[str],
) -> list[str]:
    mentions = [match.group(1) or match.group(2) or "" for match in _DOC_MENTION_RE.finditer(text)]
    mentions = [mention.strip() for mention in mentions if mention.strip()]
    if not mentions:
        return []

    existing_ids = set(existing_chunk_ids)
    docs = list_docs(account_id=account_id, user_dek=user_dek)
    extra_chunk_ids: list[str] = []
    for mention in mentions:
        mention_lower = mention.lower()
        exact = [doc for doc in docs if doc.name.lower() == mention_lower]
        matches = exact or [doc for doc in docs if mention_lower in doc.name.lower()]
        if len(matches) != 1:
            continue
        for chunk in matches[0].chunks:
            if chunk.chunk_id not in existing_ids:
                extra_chunk_ids.append(chunk.chunk_id)
                existing_ids.add(chunk.chunk_id)

    if not extra_chunk_ids:
        return []
    return get_doc_texts_for_chunk_ids(extra_chunk_ids, account_id=account_id, user_dek=user_dek)


def send_chat_message(
    session_id: str, payload: SendMessageRequest, account_id: str, user_dek: bytes | None = None
) -> SendMessageResponse:
    session = get_session(session_id, account_id=account_id, user_dek=user_dek)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    text = payload.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message must not be empty")

    lesson = get_lesson(payload.lesson_id, account_id=account_id, user_dek=user_dek) if payload.lesson_id else None
    if payload.lesson_id and lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    user_message = ChatMessage(
        role="user",
        text=text,
        created_at=datetime.now(UTC),
        lesson_id=payload.lesson_id,
        doc_chunk_ids=payload.doc_chunk_ids,
    )

    past_messages = list(session.messages)
    append_message(session_id, user_message, account_id=account_id, user_dek=user_dek)

    tool_reply = try_handle_tool_command(user_text=text, account_id=account_id, user_dek=user_dek)
    if tool_reply is not None:
        assistant_message = ChatMessage(role="assistant", text=tool_reply, created_at=datetime.now(UTC))
        result = append_message(session_id, assistant_message, account_id=account_id, user_dek=user_dek)
        if result is None:
            logger.warning("Tool-reply message may have been lost for session %s", session_id)
        return SendMessageResponse(session_id=session_id, reply=tool_reply)

    doc_texts = []
    if payload.doc_chunk_ids:
        doc_texts = get_doc_texts_for_chunk_ids(
            payload.doc_chunk_ids,
            account_id=account_id,
            user_dek=user_dek,
        )
    doc_texts.extend(
        _mentioned_doc_chunk_texts(
            text=text,
            account_id=account_id,
            user_dek=user_dek,
            existing_chunk_ids=payload.doc_chunk_ids,
        )
    )

    check_token_limit(account_id)
    reply_text, tokens = _normalize_reply_result(
        generate_reply(
            user_message=user_message,
            lesson=lesson,
            doc_texts=doc_texts,
            account_id=account_id,
            user_dek=user_dek,
            past_messages=past_messages,
            session_id=session_id,
            query_vector=payload.query_vector,
        )
    )
    record_token_usage(account_id, tokens)
    assistant_message = ChatMessage(role="assistant", text=reply_text, created_at=datetime.now(UTC))
    result = append_message(session_id, assistant_message, account_id=account_id, user_dek=user_dek)
    if result is None:
        logger.warning("Assistant message may have been lost for session %s", session_id)

    _trigger_dialectic_memory(
        session_id=session_id,
        account_id=account_id,
        past_messages=past_messages,
        user_message=user_message,
        assistant_message=assistant_message,
    )

    return SendMessageResponse(session_id=session_id, reply=reply_text)


def send_chat_message_stream(
    session_id: str, payload: SendMessageRequest, account_id: str, user_dek: bytes | None = None
):
    """Yields SSE event dicts for streaming chat responses.

    Yields dicts like:
        {"event": "token", "data": "Hello"}
        {"event": "tool", "data": "Executed tool: list_lessons"}
        {"event": "done", "data": "full reply text"}
    """
    session = get_session(session_id, account_id=account_id, user_dek=user_dek)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    text = payload.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message must not be empty")

    lesson = get_lesson(payload.lesson_id, account_id=account_id, user_dek=user_dek) if payload.lesson_id else None
    if payload.lesson_id and lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    user_message = ChatMessage(
        role="user",
        text=text,
        created_at=datetime.now(UTC),
        lesson_id=payload.lesson_id,
        doc_chunk_ids=payload.doc_chunk_ids,
    )

    past_messages = list(session.messages)
    append_message(session_id, user_message, account_id=account_id, user_dek=user_dek)

    tool_reply = try_handle_tool_command(user_text=text, account_id=account_id, user_dek=user_dek)
    if tool_reply is not None:
        assistant_message = ChatMessage(role="assistant", text=tool_reply, created_at=datetime.now(UTC))
        result = append_message(session_id, assistant_message, account_id=account_id, user_dek=user_dek)
        if result is None:
            logger.warning("Tool-reply message may have been lost for session %s", session_id)
        pending_events = pop_pending_tool_events(account_id)
        for evt in pending_events:
            evt_type = evt.get("type", "")
            if evt_type == "reasoning_delta":
                yield {"event": "thinking", "data": evt.get("delta", "")}
            elif evt_type == "tool_start":
                yield {"event": "plan_step", "data": {
                    "id": f"quiz-search-{evt.get('tool_name', '')}",
                    "title": f"Searching: {evt.get('tool_name', '')}",
                    "status": "running",
                    "detail": evt.get("query", ""),
                }}
            elif evt_type == "tool_done":
                yield {"event": "plan_step", "data": {
                    "id": f"quiz-search-{evt.get('tool_name', '')}",
                    "title": f"Searching: {evt.get('tool_name', '')}",
                    "status": "done",
                }}
        yield {"event": "token", "data": tool_reply}
        yield from _stream_lesson_job_events(tool_reply=tool_reply, account_id=account_id)
        yield {"event": "done", "data": ""}
        return

    doc_texts = []
    if payload.doc_chunk_ids:
        doc_texts = get_doc_texts_for_chunk_ids(
            payload.doc_chunk_ids,
            account_id=account_id,
            user_dek=user_dek,
        )
    doc_texts.extend(
        _mentioned_doc_chunk_texts(
            text=text,
            account_id=account_id,
            user_dek=user_dek,
            existing_chunk_ids=payload.doc_chunk_ids,
        )
    )

    check_token_limit(account_id)
    full_reply = ""
    lesson_job_id: str | None = None
    try:
        for event_type, event_data in generate_reply_stream(
            user_message=user_message,
            lesson=lesson,
            doc_texts=doc_texts,
            account_id=account_id,
            user_dek=user_dek,
            past_messages=past_messages,
            session_id=session_id,
            query_vector=payload.query_vector,
        ):
            if event_type == "token":
                full_reply += event_data
            if event_type == "tool_done" and isinstance(event_data, dict) and event_data.get("name") == "create_lesson":
                match = _LESSON_JOB_RE.search(str(event_data.get("result_preview") or ""))
                if match:
                    lesson_job_id = match.group(1)
                    yield from _stream_lesson_job_events(tool_reply=f"Job ID: {lesson_job_id}", account_id=account_id)
            yield {"event": event_type, "data": event_data}
    finally:
        if full_reply:
            record_token_usage(
                account_id,
                _estimate_turn_tokens(
                    user_text=text,
                    assistant_text=full_reply,
                    doc_texts=doc_texts,
                    past_messages=past_messages,
                ),
            )
            assistant_message = ChatMessage(role="assistant", text=full_reply, created_at=datetime.now(UTC))
            result = append_message(session_id, assistant_message, account_id=account_id, user_dek=user_dek)
            if result is None:
                logger.warning("Assistant message may have been lost for session %s", session_id)

            _trigger_dialectic_memory(
                session_id=session_id,
                account_id=account_id,
                past_messages=past_messages,
                user_message=user_message,
                assistant_message=assistant_message,
            )

    if lesson_job_id is None:
        fallback_match = _LESSON_JOB_RE.search(full_reply)
        if fallback_match:
            lesson_job_id = fallback_match.group(1)
            yield from _stream_lesson_job_events(tool_reply=f"Job ID: {lesson_job_id}", account_id=account_id)


def _stream_lesson_job_events(*, tool_reply: str, account_id: str):
    match = _LESSON_JOB_RE.search(tool_reply)
    if not match:
        return

    job_id = match.group(1)
    logger.info("chat_stream lesson_job follow job_id=%s account_id=%s", job_id, account_id)
    logger.info("lesson_generation_sse job_id=%s account_id=%s event=follow_started", job_id, account_id)
    yield {
        "event": "plan_step",
        "data": {
            "id": f"lesson-plan-{job_id}",
            "title": "Lesson build",
            "status": "waiting",
            "detail": f"Lesson generation job {job_id} accepted.",
        },
    }
    stop_signal = {
        "event": "chat_status",
        "data": {
            "status": "background",
            "job_id": job_id,
            "kind": "lesson",
            "progress": 0,
            "phase": "queued",
            "detail": "Lesson generation is continuing in the background.",
            "stop_buffering": True,
        },
    }
    if not _LESSON_JOB_STREAM_FOLLOW:
        yield stop_signal
        return

    seen_subtasks: dict[str, str] = {}
    last_phase = ""
    started_at = time.monotonic()
    while time.monotonic() - started_at < _LESSON_JOB_STREAM_TIMEOUT_SEC:
        job = get_lesson_generation_job(job_id=job_id, account_id=account_id)
        if not job:
            logger.warning("lesson_generation_sse job_id=%s account_id=%s event=missing_job", job_id, account_id)
            yield {"event": "subagent_error", "data": {"id": job_id, "title": "Lesson build", "detail": "Lesson job disappeared."}}
            return

        phase = str(job.get("phase") or job.get("status") or "")
        if phase and phase != last_phase:
            last_phase = phase
            logger.info(
                "lesson_generation_sse job_id=%s account_id=%s event=plan_step phase=%s status=%s progress=%s",
                job_id,
                account_id,
                phase,
                job.get("status"),
                job.get("progress", 0),
            )
            yield {
                "event": "plan_step",
                "data": {
                    "id": f"lesson-phase-{job_id}",
                    "title": phase.replace("_", " ").title(),
                    "status": "running" if job.get("status") not in {"succeeded", "failed", "rejected", "canceled"} else "done",
                    "detail": f"{job.get('progress', 0)}% complete",
                },
            }

        for subtask in job.get("subtasks", []) or []:
            if not isinstance(subtask, dict):
                continue
            name = str(subtask.get("name") or "")
            status = str(subtask.get("status") or "")
            phase = str(subtask.get("phase") or "")
            progress = int(subtask.get("progress") or 0)
            detail = str(subtask.get("detail") or subtask.get("error") or "")
            signature = f"{status}|{phase}|{progress}|{detail}"
            if not name or seen_subtasks.get(name) == signature:
                continue
            seen_subtasks[name] = signature
            payload = {
                "id": f"subagent-{job_id}-{name}",
                "name": name,
                "title": str(subtask.get("label") or name.replace("_", " ").title()),
                "status": status,
                "phase": phase,
                "progress": progress,
                "detail": detail,
            }
            if status in {"running", "retrying", "fallback"}:
                logger.info(
                    "lesson_generation_sse job_id=%s account_id=%s event=subagent_start name=%s status=%s phase=%s progress=%s",
                    job_id,
                    account_id,
                    name,
                    status,
                    payload["phase"],
                    payload["progress"],
                )
                yield {"event": "subagent_start", "data": payload}
            elif status == "succeeded":
                logger.info(
                    "lesson_generation_sse job_id=%s account_id=%s event=subagent_done name=%s phase=%s progress=%s",
                    job_id,
                    account_id,
                    name,
                    payload["phase"],
                    payload["progress"],
                )
                yield {"event": "subagent_done", "data": payload}
            elif status == "failed":
                logger.warning(
                    "lesson_generation_sse job_id=%s account_id=%s event=subagent_error name=%s phase=%s detail=%r",
                    job_id,
                    account_id,
                    name,
                    payload["phase"],
                    payload["detail"],
                )
                yield {"event": "subagent_error", "data": payload}
            elif status == "canceled":
                logger.info(
                    "lesson_generation_sse job_id=%s account_id=%s event=subagent_canceled name=%s phase=%s",
                    job_id,
                    account_id,
                    name,
                    payload["phase"],
                )
                yield {"event": "subagent_error", "data": {**payload, "detail": payload["detail"] or "Canceled by user."}}

        if job.get("status") in {"succeeded", "failed", "rejected", "canceled"}:
            if job.get("status") == "succeeded" and isinstance(job.get("result"), dict):
                result = job["result"]
                logger.info(
                    "lesson_generation_sse job_id=%s account_id=%s event=artifact lesson_id=%s title=%r",
                    job_id,
                    account_id,
                    result.get("lesson_id"),
                    result.get("title"),
                )
                yield {
                    "event": "artifact",
                    "data": {
                        "kind": "lesson",
                        "action": "open",
                        "job_id": job_id,
                        "lesson_id": result.get("lesson_id"),
                        "title": result.get("title"),
                    },
                }
            elif job.get("status") == "canceled":
                logger.info("lesson_generation_sse job_id=%s account_id=%s event=canceled", job_id, account_id)
                yield {
                    "event": "plan_step",
                    "data": {
                        "id": f"lesson-canceled-{job_id}",
                        "title": "Lesson build canceled",
                        "status": "done",
                        "detail": "The lesson generation job was stopped and will not be saved.",
                    },
                }
            else:
                logger.warning(
                    "lesson_generation_sse job_id=%s account_id=%s event=failed status=%s error=%r rejection=%r",
                    job_id,
                    account_id,
                    job.get("status"),
                    job.get("error"),
                    job.get("rejection_reason"),
                )
                yield {
                    "event": "plan_step",
                    "data": {
                        "id": f"lesson-failed-{job_id}",
                        "title": "Lesson build failed",
                        "status": "error",
                        "detail": str(job.get("error") or job.get("rejection_reason") or "Lesson generation did not complete."),
                    },
                }
            return
        time.sleep(_LESSON_JOB_POLL_INTERVAL_SEC)

    logger.info("lesson_generation_sse job_id=%s account_id=%s event=timeout", job_id, account_id)
    job = get_lesson_generation_job(job_id=job_id, account_id=account_id)
    progress = int(job.get("progress") or 0) if isinstance(job, dict) else 0
    phase = str(job.get("phase") or job.get("status") or "running") if isinstance(job, dict) else "running"
    yield {
        "event": "plan_step",
        "data": {
            "id": f"lesson-timeout-{job_id}",
            "title": "Lesson still building",
            "status": "waiting",
            "detail": f"The lesson job is still running in the background ({progress}% complete, {phase}).",
        },
    }
    yield {
        "event": "chat_status",
        "data": {
            "status": "background",
            "job_id": job_id,
            "kind": "lesson",
            "progress": progress,
            "phase": phase,
            "detail": "Lesson generation is continuing in the background.",
            "stop_buffering": True,
        },
    }


def confirm_chat_action(
    session_id: str,
    *,
    action_id: str,
    decision: str,
    account_id: str,
    user_dek: bytes | None = None,
) -> ConfirmActionResponse:
    session = get_session(session_id, account_id=account_id, user_dek=user_dek)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    action = pop_pending_action(action_id=action_id, session_id=session_id, account_id=account_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Pending action not found")

    if decision == "cancel":
        reply = f"Canceled: {action.get('label', action.get('tool_name', 'action'))}."
        status = "canceled"
    elif decision == "approve":
        try:
            payload = execute_tool(
                str(action["tool_name"]),
                action.get("arguments", {}) if isinstance(action.get("arguments"), dict) else {},
                account_id,
                user_dek,
            )
            reply = f"Approved and completed: {action.get('label', action.get('tool_name', 'action'))}.\n\n{payload}"
            status = "completed"
        except Exception as exc:
            reply = f"Action failed: {exc}"
            status = "failed"
    else:
        raise HTTPException(status_code=400, detail="decision must be approve or cancel")

    assistant_message = ChatMessage(role="assistant", text=reply, created_at=datetime.now(UTC))
    result = append_message(session_id, assistant_message, account_id=account_id, user_dek=user_dek)
    if result is None:
        logger.warning("Confirmation reply may have been lost for session %s", session_id)
    return ConfirmActionResponse(session_id=session_id, action_id=action_id, status=status, reply=reply)


def _trigger_dialectic_memory(
    *,
    session_id: str,
    account_id: str,
    past_messages: list[ChatMessage],
    user_message: ChatMessage,
    assistant_message: ChatMessage,
) -> None:
    from backend.app.services.memory_service import (
        get_session_summary,
        maybe_audit_user_profile,
        update_session_summary,
    )
    from backend.app.services.chat_engine import get_router

    router = get_router()
    if router is None:
        return

    recent_transcript = "\n".join(
        f"{'Assistant' if m.role == 'assistant' else 'You'}: {m.text}"
        for m in list(past_messages) + [user_message, assistant_message]
    )

    update_session_summary(
        session_id=session_id,
        old_summary=get_session_summary(session_id),
        new_messages_text=recent_transcript,
        client=router.client,
    )
    maybe_audit_user_profile(
        account_id=account_id,
        transcript=recent_transcript,
        client=router.client,
    )
