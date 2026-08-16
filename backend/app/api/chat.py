import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from backend.app.api.common import require_account, require_account_id
from backend.app.schemas import (
    ChatSession,
    ConfirmActionRequest,
    ConfirmActionResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from backend.app.services.chat_service import confirm_chat_action, send_chat_message, send_chat_message_stream
from backend.app.services.storage import create_session, get_session, list_sessions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/sessions", response_model=CreateSessionResponse)
def create_chat_session(
    _: CreateSessionRequest,
    account_id: str = Depends(require_account_id),
) -> CreateSessionResponse:
    session = create_session(account_id=account_id)
    return CreateSessionResponse(session_id=session.id, title=session.title)


@router.get("/sessions", response_model=list[ChatSession])
def list_chat_sessions(
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> list[ChatSession]:
    account_id, user_dek = auth
    return list_sessions(account_id=account_id, user_dek=user_dek, limit=20)


@router.get("/sessions/{session_id}", response_model=ChatSession)
def get_chat_session(
    session_id: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> ChatSession:
    account_id, user_dek = auth
    session = get_session(session_id, account_id=account_id, user_dek=user_dek)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse)
def send_message(
    session_id: str,
    payload: SendMessageRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> SendMessageResponse:
    account_id, user_dek = auth
    return send_chat_message(session_id=session_id, payload=payload, account_id=account_id, user_dek=user_dek)


@router.post("/sessions/{session_id}/confirm", response_model=ConfirmActionResponse)
def confirm_action(
    session_id: str,
    payload: ConfirmActionRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> ConfirmActionResponse:
    account_id, user_dek = auth
    return confirm_chat_action(
        session_id=session_id,
        action_id=payload.action_id,
        decision=payload.decision,
        account_id=account_id,
        user_dek=user_dek,
    )

def _sse_stream(session_id: str, payload: SendMessageRequest, account_id: str, user_dek: bytes | None):
    """Generator that yields SSE-formatted lines. Always emits 'done' on exit."""
    done_sent = False
    try:
        for event in send_chat_message_stream(
            session_id=session_id, payload=payload, account_id=account_id, user_dek=user_dek
        ):
            data = json.dumps({"event": event["event"], "data": event["data"], "session_id": session_id})
            yield f"data: {data}\n\n"
            if event["event"] == "done":
                done_sent = True
    except HTTPException as exc:
        error_msg = exc.detail if exc.detail else f"HTTP {exc.status_code}"
        logger.warning("SSE stream HTTP error for session %s: %s", session_id, error_msg)
        error_data = json.dumps({"event": "error", "data": error_msg, "session_id": session_id})
        yield f"data: {error_data}\n\n"
    except ValueError as exc:
        if "user_dek is required" in str(exc):
            error_msg = "Unlock required for encrypted account data. Please unlock again."
            logger.warning("SSE stream missing unlock key for session %s", session_id)
            error_data = json.dumps({"event": "error", "data": error_msg, "session_id": session_id})
            yield f"data: {error_data}\n\n"
        else:
            logger.exception("SSE stream value error for session %s", session_id)
            error_data = json.dumps({"event": "error", "data": f"Internal server error: {type(exc).__name__}", "session_id": session_id})
            yield f"data: {error_data}\n\n"
    except Exception as exc:
        logger.exception("SSE stream unexpected error for session %s", session_id)
        if isinstance(exc, OSError):
            error_msg = "Chat temporarily ran out of system resources. Please wait a moment and retry."
        else:
            error_msg = "Chat could not finish that response. Please retry."
        error_data = json.dumps({"event": "error", "data": error_msg, "session_id": session_id})
        yield f"data: {error_data}\n\n"
    finally:
        if not done_sent:
            done_data = json.dumps({"event": "done", "data": "", "session_id": session_id})
            yield f"data: {done_data}\n\n"


@router.post("/sessions/{session_id}/messages/stream")
def send_message_stream(
    session_id: str,
    payload: SendMessageRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> StreamingResponse:
    account_id, user_dek = auth
    return StreamingResponse(
        _sse_stream(session_id, payload, account_id, user_dek),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
