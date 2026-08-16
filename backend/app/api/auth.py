from __future__ import annotations

import base64
import logging
import os
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request

from backend.app.api.common import valid_account_id
from backend.app.schemas import UnlockRequest, UnlockResponse
from backend.app.services.crypto_local import (
    Argon2Params,
    derive_kek,
    generate_dek,
    parse_params,
    serialize_params,
    unwrap_dek,
    wrap_dek,
)
from backend.app.services.db import SessionLocal, cloud_db_enabled, db_enabled, db_healthcheck
from backend.app.services.models import AccountKeyModel
from engine.quiz.account_system import AccountDataKeyMismatch
from backend.app.services.unlock_session import (
    cleanup_expired_unlock_sessions,
    clear_unlock_session,
    create_unlock_session,
    key_material_fingerprint,
    resolve_dek,
)
from backend.app.services.unlock_guard import account_exists, resolve_dek_from_passphrase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _validate_client_id(request: Request) -> str:
    client_id = request.headers.get("x-client-id", "").strip()
    if not client_id or len(client_id) < 8:
        raise HTTPException(status_code=400, detail="X-Client-Id header required (min 8 chars)")
    return client_id


def _key_material_matches(left: AccountKeyModel, right: AccountKeyModel) -> bool:
    return (
        left.salt_b64 == right.salt_b64
        and left.argon2_params_json == right.argon2_params_json
        and left.wrapped_dek_b64 == right.wrapped_dek_b64
    )


def _copy_key_material(target: AccountKeyModel, source: AccountKeyModel) -> None:
    target.salt_b64 = source.salt_b64
    target.argon2_params_json = source.argon2_params_json
    target.wrapped_dek_b64 = source.wrapped_dek_b64
    target.unlock_version = source.unlock_version
    target.failed_unlock_attempts = source.failed_unlock_attempts
    target.consecutive_lockouts = source.consecutive_lockouts
    target.locked_until = source.locked_until
    target.last_failed_unlock_at = source.last_failed_unlock_at
    target.created_at = source.created_at
    target.updated_at = source.updated_at


def _key_fingerprint(row: AccountKeyModel) -> str:
    return key_material_fingerprint(
        salt_b64=str(row.salt_b64),
        argon2_params_json=str(row.argon2_params_json),
        wrapped_dek_b64=str(row.wrapped_dek_b64),
    )


def _clone_key_row(source: AccountKeyModel) -> AccountKeyModel:
    row = AccountKeyModel(account_id=source.account_id)
    _copy_key_material(row, source)
    return row


def _unwrap_key_row(row: AccountKeyModel, passphrase: str) -> bytes:
    params = parse_params(row.argon2_params_json)
    kek = derive_kek(passphrase, base64.b64decode(row.salt_b64.encode("ascii")), params)
    return unwrap_dek(kek, row.wrapped_dek_b64)


def _cloud_key_row(account_id: str) -> AccountKeyModel | None:
    from backend.app.services.db import CloudSessionLocal, cloud_db_enabled, is_sqlite_mode

    if not (is_sqlite_mode() and cloud_db_enabled()):
        return None
    assert CloudSessionLocal is not None
    with CloudSessionLocal() as cloud_db:
        return cloud_db.query(AccountKeyModel).where(AccountKeyModel.account_id == account_id).first()


def _perform_unlock(
    *,
    db,
    account_id: str,
    passphrase: str,
    request: Request,
) -> tuple[str, bytes | None]:
    """Shared unlock logic: returns (token, dek).

    If the account has no key material yet, creates it (first-time unlock).
    Otherwise derives the DEK from the passphrase.
    """
    cleanup_expired_unlock_sessions(db)
    row = db.query(AccountKeyModel).where(AccountKeyModel.account_id == account_id).first()
    cloud_row = _cloud_key_row(account_id)
    if row is None:
        if cloud_row is not None:
            row = _clone_key_row(cloud_row)
            db.add(row)
            db.commit()
            logger.info("Imported encryption key for %s from cloud DB", account_id)
    elif cloud_row is not None and not _key_material_matches(row, cloud_row):
        try:
            cloud_dek = _unwrap_key_row(cloud_row, passphrase)
        except Exception as exc:
            logger.warning(
                "Cloud/local encryption key mismatch for %s; cloud key did not unlock, falling back to local key: %s",
                account_id,
                exc,
            )
        else:
            _copy_key_material(row, cloud_row)
            db.add(row)
            db.commit()
            logger.warning("Replaced stale local encryption key for %s with cloud key material", account_id)
            token = create_unlock_session(
                account_id=account_id,
                dek=cloud_dek,
                unlock_version=row.unlock_version,
                request=request,
                db=db,
                key_material_fingerprint=_key_fingerprint(row),
            )
            return token, cloud_dek
    if row is None:
        if not account_exists(account_id):
            raise HTTPException(status_code=404, detail="Account not found")
        params = Argon2Params()
        salt = base64.b64encode(os.urandom(16)).decode("ascii")
        kek = derive_kek(
            passphrase, base64.b64decode(salt.encode("ascii")), params
        )
        dek = generate_dek()
        row = AccountKeyModel(
            account_id=account_id,
            salt_b64=salt,
            argon2_params_json=serialize_params(params),
            wrapped_dek_b64=wrap_dek(kek, dek),
            unlock_version=1,
            failed_unlock_attempts=0,
            consecutive_lockouts=0,
            locked_until=None,
            last_failed_unlock_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(row)
        db.commit()
        token = create_unlock_session(
            account_id=account_id,
            dek=dek,
            unlock_version=row.unlock_version,
            request=request,
            db=db,
            key_material_fingerprint=_key_fingerprint(row),
        )
        return token, dek

    dek = resolve_dek_from_passphrase(db, account_id, passphrase)
    token = create_unlock_session(
        account_id=account_id,
        dek=dek,
        unlock_version=row.unlock_version,
        request=request,
        db=db,
        key_material_fingerprint=_key_fingerprint(row),
    )
    return token, dek


@router.post("/unlock", response_model=UnlockResponse)
def unlock_account(
    payload: UnlockRequest,
    request: Request,
    account_id: str = Depends(valid_account_id),
) -> UnlockResponse:
    _validate_client_id(request)
    if not db_enabled():
        raise HTTPException(status_code=400, detail="DB mode required for unlock")
    assert SessionLocal is not None
    with SessionLocal() as db:
        token, _dek = _perform_unlock(db=db, account_id=account_id, passphrase=payload.passphrase, request=request)
    return UnlockResponse(token=token)


@router.post("/lock")
def lock_account(
    request: Request,
    account_id: str = Depends(valid_account_id),
    x_unlock_token: str | None = Header(default=None),
) -> dict[str, str]:
    if not db_enabled():
        return {"status": "ok"}
    assert SessionLocal is not None
    with SessionLocal() as db:
        row = db.query(AccountKeyModel).where(AccountKeyModel.account_id == account_id).first()
        if row is None:
            return {"status": "ok"}
        dek = resolve_dek(
            account_id=account_id,
            token=x_unlock_token,
            unlock_version=row.unlock_version,
            request=request,
            db=db,
            key_material_fingerprint=_key_fingerprint(row),
        )

        # Push local data to cloud before locking (SQLite mode only, requires valid token)
        from backend.app.services.db import is_sqlite_mode
        from backend.app.services.account_sync import push_account_to_cloud
        if is_sqlite_mode() and dek is not None:
            try:
                push_report = push_account_to_cloud(account_id, dek)
                logger.info(
                    "Lock-time sync push for %s: %d chat, %d rewards, %d files",
                    account_id, push_report.chat_sessions, push_report.reward_sessions, push_report.uploaded_files,
                )
            except Exception as exc:
                logger.warning("Lock-time sync push failed for %s: %s", account_id, exc)

        clear_unlock_session(account_id=account_id, token=x_unlock_token, db=db)
        row.unlock_version = int(row.unlock_version or 0) + 1
        row.updated_at = datetime.now(UTC)
        db.add(row)
        db.commit()
    return {"status": "ok"}


from pydantic import BaseModel, field_validator
from engine.quiz.account_system import AccountStore
import pathlib

_BASE_DIR = pathlib.Path(__file__).resolve().parents[3]


class UserAuthPayload(BaseModel):
    username: str
    pin: str

    @field_validator("pin")
    @classmethod
    def validate_pin(cls, v: str) -> str:
        v = v.strip()
        if v and (not v.isdigit() or len(v) < 4 or len(v) > 12):
            raise ValueError("PIN must be 4-12 digits")
        return v


@router.get("/account-exists/{username}")
def account_exists_for_login(username: str) -> dict:
    store = AccountStore(_BASE_DIR / "engine" / "accounts" / "accounts.json")
    return {"username": username.strip().lower(), "exists": store.user_exists(username)}


@router.post("/register")
def register_user(payload: UserAuthPayload) -> dict:
    store = AccountStore(_BASE_DIR / "engine" / "accounts" / "accounts.json")
    if store.user_exists(payload.username):
        raise HTTPException(status_code=400, detail="Username already exists")
    try:
        profile = store.create_user(username=payload.username, pin=payload.pin)
        return {"status": "ok", "username": payload.username}
    except Exception as exc:
        logger.exception("Registration failed for %s", payload.username)
        raise HTTPException(status_code=400, detail="Registration failed")


@router.post("/login")
def login_user(payload: UserAuthPayload) -> dict:
    store = AccountStore(_BASE_DIR / "engine" / "accounts" / "accounts.json")
    if not store.user_exists(payload.username):
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        profile = store.verify_login(username=payload.username, pin=payload.pin)
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    if profile is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"status": "ok", "username": payload.username}


class BootstrapRequest(BaseModel):
    passphrase: str


@router.post("/bootstrap")
def bootstrap_account(
    payload: BootstrapRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    account_id: str = Depends(valid_account_id),
) -> dict:
    """Single endpoint for post-login setup: unlock + sync + fetch lessons.

    Docs are fetched lazily by the frontend via GET /docs.
    Returns everything the UI needs to render the shell state.
    """
    from backend.app.api.common import active_user_dek, normalize_account_id
    from backend.app.services.account_sync import hydrate_account_artifacts, pull_account_from_cloud
    from backend.app.services.db import is_sqlite_mode
    from backend.app.services.storage import list_account_lessons

    _validate_client_id(request)
    normalized = normalize_account_id(account_id)

    #keep the user updated
    # --- Unlock ---
    token = None
    user_dek = None
    if db_enabled():
        assert SessionLocal is not None
        with SessionLocal() as db:
            token, user_dek = _perform_unlock(db=db, account_id=normalized, passphrase=payload.passphrase, request=request)

    # --- Sync account registry synchronously; hydrate large artifacts in background ---
    sync_result = None
    sync_required = bool(db_enabled() and token and is_sqlite_mode() and cloud_db_enabled())
    try:
        if sync_required:
            from backend.app.api.common import resolve_dek
            assert SessionLocal is not None
            with SessionLocal() as db:
                row = db.query(AccountKeyModel).where(AccountKeyModel.account_id == normalized).first()
                if row:
                    dek_for_sync = resolve_dek(
                        account_id=normalized,
                        token=token,
                        unlock_version=row.unlock_version,
                        request=request,
                        db=db,
                        key_material_fingerprint=_key_fingerprint(row),
                    )
                    if not dek_for_sync:
                        raise RuntimeError("Unlock session could not be resolved for account sync")
                    report = pull_account_from_cloud(normalized, dek_for_sync, content_mode="essentials")
                    sync_result = {
                        "status": "ready",
                        "registry_synced": bool(report.registry_synced),
                    }
                    import threading
                    def _background_hydration(account: str, dek: bytes) -> None:
                        try:
                            hydrate_account_artifacts(account, dek)
                        except Exception as exc:
                            logger.warning("Bootstrap background hydration failed: %s", exc)
                    threading.Thread(target=_background_hydration, args=(normalized, dek_for_sync), daemon=True).start()
    except Exception as exc:
        logger.warning("Bootstrap account registry sync failed: %s", exc)
        if sync_required:
            raise HTTPException(
                status_code=503,
                detail="Account data sync could not be completed with the active unlock key. Please retry unlock.",
            ) from exc

    # --- Fetch lessons only (docs fetched lazily by frontend via GET /docs) ---
    token_ctx = active_user_dek.set(user_dek)
    try:
        try:
            lessons = list_account_lessons(account_id=normalized, user_dek=user_dek)
        except AccountDataKeyMismatch as exc:
            # Bootstrap is the first authenticated read on a fresh tab.  A
            # legacy profile encrypted with another DEK must invalidate the
            # session cleanly instead of escaping as an ASGI 500.
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc
    finally:
        active_user_dek.reset(token_ctx)

    # --- Cloud status ---
    db_health = db_healthcheck()
    is_cloud = bool(db_health.get("enabled") and db_health.get("ok"))

    return {
        "token": token,
        "is_cloud": is_cloud,
        "sync": sync_result,
        "lessons": [l.model_dump() for l in lessons],
        "docs": [],
    }
