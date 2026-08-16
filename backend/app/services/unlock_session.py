from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Request
from sqlalchemy import delete

from backend.app.services.models import AccountUnlockSessionModel

_TTL_MINUTES = int(os.getenv("UNLOCK_TOKEN_TTL_MINUTES", "30") or "30")
_SECRET_ENV_NAME = "UNLOCK_TOKEN_SECRET"
_TOKEN_MAX_FAILED_ATTEMPTS = int(os.getenv("UNLOCK_TOKEN_MAX_FAILED_ATTEMPTS", "20"))
_TOKEN_FAILURE_WINDOW_SECONDS = int(os.getenv("UNLOCK_TOKEN_FAILURE_WINDOW_SECONDS", "60"))
_TOKEN_LOCKOUT_SECONDS = int(os.getenv("UNLOCK_TOKEN_LOCKOUT_SECONDS", "30"))

_fernet: Fernet | None = None
_token_failures: dict[tuple[str, str], tuple[float, int, float]] = {}


class MissingClientBindingError(ValueError):
    """Raised when an unlock session cannot be bound to a stable client id."""


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    raw_secret = os.getenv(_SECRET_ENV_NAME, "").strip()
    if not raw_secret:
        raise RuntimeError(f"{_SECRET_ENV_NAME} must be set for unlock tokens")
    key_material = hashlib.pbkdf2_hmac(
        "sha256",
        raw_secret.encode("utf-8"),
        b"obital-unlock-fernet",
        100_000,
        dklen=32,
    )
    _fernet = Fernet(base64.urlsafe_b64encode(key_material))
    return _fernet


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def compute_key_material_fingerprint(*, salt_b64: str, argon2_params_json: str, wrapped_dek_b64: str) -> str:
    """Return a non-secret identifier for the account's current key material."""
    material = "\x00".join((salt_b64, argon2_params_json, wrapped_dek_b64)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


key_material_fingerprint = compute_key_material_fingerprint


def _encrypt_dek(dek: bytes) -> str:
    return _get_fernet().encrypt(base64.b64encode(dek)).decode("ascii")


def _decrypt_dek(payload: str) -> bytes | None:
    try:
        raw = _get_fernet().decrypt(payload.encode("ascii"))
        return base64.b64decode(raw)
    except (InvalidToken, ValueError):
        return None


def _client_binding(request: Request) -> tuple[str, str]:
    client_id = request.headers.get("x-client-id", "").strip()
    user_agent = request.headers.get("user-agent", "").strip()
    return client_id, user_agent


def _require_client_id(request: Request) -> tuple[str, str]:
    client_id, user_agent = _client_binding(request)
    if not client_id:
        raise MissingClientBindingError("X-Client-Id header required")
    if not user_agent:
        raise MissingClientBindingError("User-Agent header required")
    return client_id, user_agent


def _token_failure_key(account_id: str, client_id: str) -> tuple[str, str]:
    return account_id, client_id or "missing-client"


def _enforce_token_window(account_id: str, client_id: str) -> None:
    key = _token_failure_key(account_id, client_id)
    now = time.monotonic()
    first_seen, _count, locked_until = _token_failures.get(key, (now, 0, 0.0))
    if locked_until > now:
        from fastapi import HTTPException
        raise HTTPException(status_code=429, detail="Unlock token temporarily rate limited")
    if now - first_seen > _TOKEN_FAILURE_WINDOW_SECONDS:
        _token_failures.pop(key, None)


def _record_token_failure(account_id: str, client_id: str) -> None:
    key = _token_failure_key(account_id, client_id)
    now = time.monotonic()
    first_seen, count, locked_until = _token_failures.get(key, (now, 0, 0.0))
    if now - first_seen > _TOKEN_FAILURE_WINDOW_SECONDS:
        first_seen = now
        count = 0
        locked_until = 0.0
    count += 1
    if count >= _TOKEN_MAX_FAILED_ATTEMPTS:
        locked_until = now + _TOKEN_LOCKOUT_SECONDS
        count = 0
        first_seen = now
    _token_failures[key] = (first_seen, count, locked_until)


def _record_token_success(account_id: str, client_id: str) -> None:
    _token_failures.pop(_token_failure_key(account_id, client_id), None)


def cleanup_expired_unlock_sessions(db) -> int:
    result = db.execute(
        delete(AccountUnlockSessionModel).where(AccountUnlockSessionModel.expires_at < datetime.now(UTC))
    )
    db.commit()
    return result.rowcount


def create_unlock_session(
    account_id: str,
    dek: bytes,
    unlock_version: int,
    request: Request,
    db,
    key_material_fingerprint: str = "",
) -> str:
    if not key_material_fingerprint:
        # Keep direct callers safe too: newly-created sessions should carry a
        # fingerprint even when they did not explicitly pass one.
        try:
            from backend.app.services.models import AccountKeyModel
            key_row = db.query(AccountKeyModel).where(AccountKeyModel.account_id == account_id).first()
            if key_row is not None:
                key_material_fingerprint = compute_key_material_fingerprint(
                    salt_b64=str(key_row.salt_b64),
                    argon2_params_json=str(key_row.argon2_params_json),
                    wrapped_dek_b64=str(key_row.wrapped_dek_b64),
                )
        except Exception:
            pass
    token = secrets.token_urlsafe(32)
    client_id, user_agent = _require_client_id(request)
    row = AccountUnlockSessionModel(
        token_hash=_token_hash(token),
        account_id=account_id,
        dek_ciphertext_b64=_encrypt_dek(dek),
        unlock_version=int(unlock_version),
        key_material_fingerprint=key_material_fingerprint,
        client_id=client_id,
        user_agent=user_agent[:512],
        expires_at=datetime.now(UTC) + timedelta(minutes=_TTL_MINUTES),
        created_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    return token


def resolve_dek(
    account_id: str,
    token: str | None,
    unlock_version: int,
    request: Request,
    db,
    key_material_fingerprint: str = "",
) -> bytes | None:
    if not token:
        return None
    client_id, user_agent = _client_binding(request)
    _enforce_token_window(account_id, client_id)
    row = db.query(AccountUnlockSessionModel).where(AccountUnlockSessionModel.token_hash == _token_hash(token)).first()
    if row is None:
        _record_token_failure(account_id, client_id)
        return None
    if not secrets.compare_digest(str(row.account_id), account_id) or int(row.unlock_version) != int(unlock_version):
        _record_token_failure(account_id, client_id)
        return None
    # Legacy sessions have no fingerprint and are rejected once the current
    # account fingerprint is supplied, forcing a fresh unlock after deploy.
    if key_material_fingerprint and not secrets.compare_digest(
        str(getattr(row, "key_material_fingerprint", "")), key_material_fingerprint
    ):
        _record_token_failure(account_id, client_id)
        return None
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if datetime.now(UTC) >= expires:
        _record_token_failure(account_id, client_id)
        return None
    if not row.client_id:
        _record_token_failure(account_id, client_id)
        return None
    if not client_id or not secrets.compare_digest(str(row.client_id), client_id):
        _record_token_failure(account_id, client_id)
        return None
    if not row.user_agent:
        _record_token_failure(account_id, client_id)
        return None
    if not user_agent or not secrets.compare_digest(str(row.user_agent), user_agent):
        _record_token_failure(account_id, client_id)
        return None
    dek = _decrypt_dek(row.dek_ciphertext_b64)
    if dek is None:
        _record_token_failure(account_id, client_id)
    else:
        _record_token_success(account_id, client_id)
    return dek


def resolve_dek_for_account(account_id: str, db) -> bytes | None:
    """Resolve DEK for an account using any active unlock session.

    Used by the background sync scheduler which has no request context.
    Returns None if no active session exists or decryption fails.
    """
    from backend.app.services.models import AccountKeyModel

    key_row = db.query(AccountKeyModel).where(AccountKeyModel.account_id == account_id).first()
    if key_row is None:
        return None
    fingerprint = compute_key_material_fingerprint(
        salt_b64=str(key_row.salt_b64),
        argon2_params_json=str(key_row.argon2_params_json),
        wrapped_dek_b64=str(key_row.wrapped_dek_b64),
    )
    row = (
        db.query(AccountUnlockSessionModel)
        .where(
            AccountUnlockSessionModel.account_id == account_id,
            AccountUnlockSessionModel.expires_at > datetime.now(UTC),
            AccountUnlockSessionModel.unlock_version == int(key_row.unlock_version),
            AccountUnlockSessionModel.key_material_fingerprint == fingerprint,
        )
        .order_by(AccountUnlockSessionModel.created_at.desc())
        .first()
    )
    if row is None:
        return None
    if not row.client_id:
        return None
    if int(getattr(row, "unlock_version", -1)) != int(key_row.unlock_version):
        return None
    if not secrets.compare_digest(str(getattr(row, "key_material_fingerprint", "")), fingerprint):
        return None
    return _decrypt_dek(row.dek_ciphertext_b64)


def clear_unlock_session(account_id: str, token: str | None, db) -> None:
    if not token:
        return
    row = db.query(AccountUnlockSessionModel).where(AccountUnlockSessionModel.token_hash == _token_hash(token)).first()
    if row is None:
        return
    if row.account_id != account_id:
        return
    db.delete(row)
    db.commit()
