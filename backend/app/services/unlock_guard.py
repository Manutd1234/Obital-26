from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import pathlib
import time as _time
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

from cryptography.exceptions import InvalidTag
from fastapi import HTTPException

from backend.app.services.crypto_local import derive_kek, parse_params, unwrap_dek
from backend.app.services.models import AccountKeyModel
from engine.quiz.account_system import AccountStore

_MAX_FAILED_UNLOCK_ATTEMPTS = int(os.getenv("UNLOCK_MAX_FAILED_ATTEMPTS", "5"))
_LOCKOUT_MINUTES = int(os.getenv("UNLOCK_LOCKOUT_MINUTES", "15"))
_MAX_LOCKOUT_MINUTES = 240
_BASE_DIR = pathlib.Path(__file__).resolve().parents[3]

_account_exists_cache: dict[str, tuple[float, bool]] = {}
_account_exists_cache_ttl: float = 30.0


def account_exists(account_id: str) -> bool:
    now = _time.monotonic()
    cached = _account_exists_cache.get(account_id)
    if cached is not None and now - cached[0] < _account_exists_cache_ttl:
        return cached[1]
    if len(_account_exists_cache) > 256:
        _account_exists_cache.clear()

    # Query database directly if enabled
    try:
        from backend.app.services.db import db_enabled, SessionLocal
        from backend.app.services.models import AccountRegistryRecordModel
        if db_enabled() and SessionLocal is not None:
            with SessionLocal() as db:
                row = db.query(AccountRegistryRecordModel).where(AccountRegistryRecordModel.account_id == account_id).first()
                if row is not None:
                    _account_exists_cache[account_id] = (now, True)
                    return True
    except Exception as exc:
        logger.warning("account_exists DB check error: %s", exc)

    store = AccountStore(_BASE_DIR / "engine" / "accounts" / "accounts.json")
    result = store.get_user(account_id) is not None
    _account_exists_cache[account_id] = (now, result)
    return result


def enforce_unlock_window(row: AccountKeyModel) -> None:
    if row.locked_until is not None and datetime.now(UTC) < row.locked_until:
        raise HTTPException(status_code=429, detail="Unlock temporarily locked")


def record_unlock_failure(db, account_id: str) -> None:
    now = datetime.now(UTC)
    row = db.query(AccountKeyModel).with_for_update().where(AccountKeyModel.account_id == account_id).first()
    if row is None:
        return
    row.failed_unlock_attempts = int(row.failed_unlock_attempts or 0) + 1
    row.last_failed_unlock_at = now
    row.updated_at = now
    if row.failed_unlock_attempts >= _MAX_FAILED_UNLOCK_ATTEMPTS:
        backoff_minutes = min(_LOCKOUT_MINUTES * (2 ** (int(row.consecutive_lockouts or 0))), _MAX_LOCKOUT_MINUTES)
        row.locked_until = now.replace(microsecond=0) + timedelta(minutes=backoff_minutes)
        row.failed_unlock_attempts = 0
        row.consecutive_lockouts = int(row.consecutive_lockouts or 0) + 1
    db.add(row)
    db.commit()


def record_unlock_success(db, account_id: str) -> None:
    now = datetime.now(UTC)
    row = db.query(AccountKeyModel).with_for_update().where(AccountKeyModel.account_id == account_id).first()
    if row is None:
        return
    row.failed_unlock_attempts = 0
    row.locked_until = None
    row.last_failed_unlock_at = None
    row.consecutive_lockouts = 0
    row.updated_at = now
    db.add(row)
    db.commit()


def resolve_dek_from_passphrase(db, account_id: str, passphrase: str) -> bytes:
    row = db.query(AccountKeyModel).where(AccountKeyModel.account_id == account_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No key material for account")
    enforce_unlock_window(row)
    try:
        params = parse_params(row.argon2_params_json)
        kek = derive_kek(passphrase, base64.b64decode(row.salt_b64.encode("ascii")), params)
        dek = unwrap_dek(kek, row.wrapped_dek_b64)
    except (ValueError, TypeError, binascii.Error, InvalidTag, json.decoder.JSONDecodeError) as exc:
        record_unlock_failure(db, account_id)
        raise HTTPException(status_code=401, detail="Invalid passphrase") from exc
    record_unlock_success(db, account_id)
    return dek
