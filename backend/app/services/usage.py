import json
import logging
import threading
from datetime import datetime, UTC, timedelta
from pathlib import Path

from fastapi import HTTPException,status

from engine.quiz.account_system import AccountStore

_usage_lock = threading.Lock()

MAX_TOKENS = 100_000
MAX_DOCS = 50
MAX_LESSONS = 100
MAX_STORAGE_BYTES = 100 * 1024 * 1024

BASE_DIR = Path(__file__).resolve().parents[3]
ACCOUNTS_PATH = BASE_DIR / "engine" / "accounts" / "accounts.json"

RESET_PERIOD = timedelta(days=30)

def _maybe_reset_usage(usage: dict) -> dict:
    reset_at = usage.get("usage_reset_at")
    if reset_at is None:
        usage["usage_reset_at"] = datetime.now(UTC).isoformat()
        return usage
    last_reset = datetime.fromisoformat(reset_at)
    if last_reset.tzinfo is None:
        last_reset = last_reset.replace(tzinfo=UTC)
    if datetime.now(UTC) - last_reset > RESET_PERIOD:
        usage["token_used"] = 0
        usage["usage_reset_at"] = datetime.now(UTC).isoformat()
    return usage

def _account_store() -> AccountStore:
    return AccountStore(ACCOUNTS_PATH)

def _default_usage() -> dict:
    return {
        "token_used" : 0,
        "usage_reset_at" : None,
        "doc_count" : 0,
        "lesson_count" : 0,
        "storage_bytes" : 0,
    }

def get_usage(account_id: str) -> dict:
    store = _account_store()
    user = store.get_user(account_id)
    if user is None:
        return dict(_default_usage())
    raw = user.get("usage", {})
    if not isinstance(raw, dict):
        return dict(_default_usage())
    usage = {**_default_usage(), **raw}
    return _maybe_reset_usage(usage)

def _save_usage(account_id: str, usage: dict) -> None:
    from backend.app.services.db import SessionLocal, is_db_mode
    from backend.app.services.models import AccountRegistryRecordModel
    if is_db_mode():
        if SessionLocal is None:
            raise RuntimeError("Database mode is enabled but SessionLocal is unavailable.")
        with SessionLocal() as db:
            row = db.query(AccountRegistryRecordModel).where(
                AccountRegistryRecordModel.account_id == account_id
            ).first()
            if row is not None:
                profile = json.loads(row.profile_json)
            else:
                profile = {"username": account_id}
            profile["usage"] = usage
            profile_str = json.dumps(profile, ensure_ascii=True)
            if row is None:
                row = AccountRegistryRecordModel(
                    account_id=account_id,
                    profile_json=profile_str,
                    updated_at=datetime.now(UTC),
                )
                db.add(row)
            else:
                row.profile_json = profile_str
                row.updated_at = datetime.now(UTC)
            db.commit()
    else:
        store = _account_store()
        payload = store._load()
        users = payload.setdefault("users", {})
        profile = users.get(account_id)
        if profile is None:
            profile = {"username": account_id}
        profile["usage"] = usage
        users[account_id] = profile
        store._save(payload)

def record_token_usage(account_id: str, token: int) -> dict:
    with _usage_lock:
        usage = get_usage(account_id)
        usage["token_used"] = usage.get("token_used", 0) + token
        _save_usage(account_id, usage)
        return dict(usage)

def record_doc_upload(account_id: str, doc: int = 0, bytes_added: int = 0) -> dict:
    with _usage_lock:
        usage = get_usage(account_id)
        usage["doc_count"] = usage.get("doc_count", 0) + doc
        usage["storage_bytes"] = usage.get("storage_bytes", 0) + bytes_added
        _save_usage(account_id, usage)
        return dict(usage)

def record_lesson_upload(account_id: str, lesson: int) -> dict:
    with _usage_lock:
        usage = get_usage(account_id)
        usage["lesson_count"] = usage.get("lesson_count", 0) + lesson
        _save_usage(account_id, usage)
        return dict(usage)

def check_token_limit(account_id: str) -> None:
    usage = get_usage(account_id)
    if usage.get("token_used", 0) >= MAX_TOKENS:
        raise HTTPException(
            status_code = status.HTTP_429_TOO_MANY_REQUESTS,
            detail = f"Token limit of {MAX_TOKENS} reached",
        )

def check_doc_limit(account_id: str, content_size: int = 0) -> None:
    usage = get_usage(account_id)
    if usage.get("doc_count", 0) >= MAX_DOCS:
        raise HTTPException(
            status_code = status.HTTP_429_TOO_MANY_REQUESTS,
            detail = f"Document limit of {MAX_DOCS} reached",
        )

    if usage.get("storage_bytes", 0) + content_size >= MAX_STORAGE_BYTES:
        raise HTTPException(
            status_code = status.HTTP_429_TOO_MANY_REQUESTS,
            detail = f"Storage limit of {MAX_STORAGE_BYTES // (1024 * 1024)} MB reached",
        )

def check_lesson_limit(account_id: str) -> None:
    usage = get_usage(account_id)
    if usage.get("lesson_count", 0) >= MAX_LESSONS:
        raise HTTPException(
            status_code = status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Lesson limit of {MAX_LESSONS} reached",
        )

def estimate_tokens(text: str) -> int:
   return max(1, len(text) // 4)
