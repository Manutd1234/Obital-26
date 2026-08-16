from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.api.common import require_account
from backend.app.schemas import AccountUsage, RecordTokensRequest
from backend.app.services.usage import (
    MAX_DOCS,
    MAX_LESSONS,
    MAX_STORAGE_BYTES,
    MAX_TOKENS,
    check_token_limit,
    get_usage,
    record_token_usage,
)

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=AccountUsage)
def get_account_usage(
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> AccountUsage:
    usage = get_usage(auth[0])
    return AccountUsage(
        tokens_used=usage.get("token_used", 0),
        doc_count=usage.get("doc_count", 0),
        lessons_count=usage.get("lesson_count", 0),
        storage_bytes=usage.get("storage_bytes", 0),
        max_tokens=MAX_TOKENS,
        max_docs=MAX_DOCS,
        max_lessons=MAX_LESSONS,
        max_storage_bytes=MAX_STORAGE_BYTES,
    )


@router.post("/tokens")
def record_tokens(
    payload: RecordTokensRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    account_id, _user_dek = auth
    check_token_limit(account_id)
    usage = record_token_usage(account_id, payload.tokens)
    return {"status": "ok", "tokens_used": usage["token_used"]}
