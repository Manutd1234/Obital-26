from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from backend.app.api.common import require_account
from backend.app.services.account_sync import (
    get_hydration_status,
    pull_account_from_cloud,
    push_account_to_cloud,
    SyncReport,
)
from backend.app.services.db import db_enabled

router = APIRouter(prefix="/sync", tags=["sync"])
logger = logging.getLogger(__name__)


@router.post("/push")
def sync_push(
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    if not db_enabled():
        raise HTTPException(status_code=400, detail="DB mode required for sync")
    account_id, dek = auth
    try:
        report = push_account_to_cloud(account_id, dek)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected sync push failure for account '%s'", account_id)
        raise HTTPException(status_code=500, detail="Unexpected sync push failure") from exc
    return {
        "status": "ok",
        "chat_sessions": report.chat_sessions,
        "reward_sessions": report.reward_sessions,
        "uploaded_files": report.uploaded_files,
        "deleted_remote_files": report.deleted_remote_files,
        "registry_synced": report.registry_synced,
    }


@router.post("/pull")
def sync_pull(
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    if not db_enabled():
        raise HTTPException(status_code=400, detail="DB mode required for sync")
    account_id, dek = auth
    try:
        report = pull_account_from_cloud(account_id, dek)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected sync pull failure for account '%s'", account_id)
        raise HTTPException(status_code=500, detail="Unexpected sync pull failure") from exc
    return {
        "status": "ok",
        "chat_sessions": report.chat_sessions,
        "reward_sessions": report.reward_sessions,
        "downloaded_files": report.downloaded_files,
        "deleted_local_files": report.deleted_local_files,
        "registry_synced": report.registry_synced,
    }


@router.get("/status")
def sync_status(
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    if not db_enabled():
        raise HTTPException(status_code=400, detail="DB mode required for sync")
    account_id, _ = auth
    return {"status": "ok", "hydration": get_hydration_status(account_id)}
