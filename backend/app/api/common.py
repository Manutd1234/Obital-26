import os
import contextvars

from fastapi import Depends, Header, HTTPException, Query, Request, status

from engine.shared.account_paths import normalize_account_id
from backend.app.services.db import SessionLocal, db_enabled, is_sqlite_mode
from backend.app.services.models import AccountKeyModel
from backend.app.services.unlock_session import key_material_fingerprint, resolve_dek

active_user_dek = contextvars.ContextVar("active_user_dek", default=None)


def _key_fingerprint(row: AccountKeyModel) -> str:
    return key_material_fingerprint(
        salt_b64=str(row.salt_b64),
        argon2_params_json=str(row.argon2_params_json),
        wrapped_dek_b64=str(row.wrapped_dek_b64),
    )


def handle_user_error(exc: RuntimeError, *, not_found: bool = False) -> HTTPException:
    """Convert a RuntimeError from service/quiz layers into a consistent HTTPException.

    Args:
        exc: The caught RuntimeError.
        not_found: If True, return 404 (the resource was not found).
                   If False, return 400 (bad request / business rule violation).
    """
    if str(exc).startswith("ACCOUNT_KEY_MISMATCH:"):
        return HTTPException(status_code=409, detail=str(exc))
    if not_found:
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _is_local_single_user() -> bool:
    return os.getenv("OBITAL_LOCAL_SINGLE_USER", "").strip() == "1"


def _validate_account_id_format(account_id: str) -> str:
    try:
        return normalize_account_id(account_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _resolve_token_dek(
    *,
    request: Request,
    account_id: str,
    x_unlock_token: str,
) -> tuple[str, bytes]:
    """Validate the unlock token against the DB and return (authoritative_account_id, dek)."""
    if not db_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database required for token authentication",
        )
    assert SessionLocal is not None
    with SessionLocal() as db:
        row = db.query(AccountKeyModel).where(AccountKeyModel.account_id == account_id).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account not configured",
            )
        dek = resolve_dek(
            account_id=account_id,
            token=x_unlock_token,
            unlock_version=row.unlock_version,
            request=request,
            db=db,
            key_material_fingerprint=_key_fingerprint(row),
        )
        if dek is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired unlock token",
            )
        return (row.account_id, dek)


async def require_account(
    request: Request,
    account_id: str = Query(...),
    x_unlock_token: str | None = Header(default=None),
) -> tuple[str, bytes | None]:
    """Require a valid unlock token and return (account_id, dek).

    In normal (cloud/deploy) mode: validates the token against the DB,
    asserts account_id matches the token's session, and returns the
    token-bound account_id and DEK.

    In OBITAL_LOCAL_SINGLE_USER mode: if a valid unlock token is
    provided and a DB is available, resolves the DEK for transparent
    encryption/decryption. Otherwise returns (normalized_id, None).

    Use this on every account-scoped route.
    """
    normalized = _validate_account_id_format(account_id)

    if _is_local_single_user():
        if x_unlock_token and db_enabled():
            try:
                account_id_bound, dek = _resolve_token_dek(
                    request=request,
                    account_id=normalized,
                    x_unlock_token=x_unlock_token,
                )
                active_user_dek.set(dek)
                return (account_id_bound, dek)
            except HTTPException:
                if not is_sqlite_mode():
                    raise
                pass
        if db_enabled() and not is_sqlite_mode():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unlock required for encrypted account data",
            )
        return (normalized, None)

    if not x_unlock_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Unlock-Token header required",
        )

    account_id_bound, dek = _resolve_token_dek(
        request=request,
        account_id=normalized,
        x_unlock_token=x_unlock_token,
    )
    active_user_dek.set(dek)
    return (account_id_bound, dek)


def require_account_id(
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> str:
    """Like require_account but returns only the account_id.

    Use on routes that need authentication but not the DEK
    (e.g. usage counters, lesson start/restart, reward session start).
    """
    return auth[0]


# ── Legacy deps (kept for auth.py / bootstrap which create tokens) ──

def valid_account_id(account_id: str = Query("admin")) -> str:
    """Validate account_id format. No authentication — use require_account instead."""
    return _validate_account_id_format(account_id)


async def optional_user_dek(
    request: Request,
    account_id: str = Query("admin"),
    x_unlock_token: str | None = Header(default=None),
) -> bytes | None:
    """Return DEK if token is present and valid, else None.

    Legacy — prefer require_account which mandates the token.
    """
    if not x_unlock_token:
        return None
    try:
        normalized = normalize_account_id(account_id)
    except RuntimeError:
        return None
    if not db_enabled():
        return None
    assert SessionLocal is not None
    try:
        with SessionLocal() as db:
            row = db.query(AccountKeyModel).where(AccountKeyModel.account_id == normalized).first()
            if row is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid unlock token")
            dek = resolve_dek(
                account_id=normalized,
                token=x_unlock_token,
                unlock_version=row.unlock_version,
                request=request,
                db=db,
                key_material_fingerprint=_key_fingerprint(row),
            )
            if dek is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid unlock token")
            active_user_dek.set(dek)
            return dek
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unlock validation failed")
