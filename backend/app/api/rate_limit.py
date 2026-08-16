from __future__ import annotations

import logging
import time
import threading
from collections import defaultdict
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, status
from sqlalchemy import text

from backend.app.api.common import require_account
from backend.app.services.db import SessionLocal, db_enabled

logger = logging.getLogger(__name__)

_LIMITS: dict[str, int] = {
    "pipeline": 2,
    "quiz_build": 5,
}

_WINDOW_SECONDS = 60
_BUCKETS: dict[str, list[float]] = defaultdict(list)
_lock = threading.Lock()


def _key(account_id: str, bucket: str) -> str:
    return f"{account_id}:{bucket}"


def _check_and_increment(account_id: str, bucket: str) -> None:
    if db_enabled() and SessionLocal is not None:
        _check_and_increment_db(account_id, bucket)
        return

    now = time.monotonic()
    k = _key(account_id, bucket)
    limit = _LIMITS.get(bucket, 5)
    with _lock:
        cutoff = now - _WINDOW_SECONDS
        _BUCKETS[k] = [t for t in _BUCKETS[k] if t > cutoff]
        if len(_BUCKETS[k]) >= limit:
            retry_after = int(_WINDOW_SECONDS - (now - _BUCKETS[k][0]))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {bucket}. Retry after {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )
        _BUCKETS[k].append(now)


def _check_and_increment_db(account_id: str, bucket: str) -> None:
    now = time.time()
    window_start = int(now // _WINDOW_SECONDS) * _WINDOW_SECONDS
    k = _key(account_id, bucket)
    limit = _LIMITS.get(bucket, 5)
    updated_at = datetime.now(UTC)
    assert SessionLocal is not None

    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                INSERT INTO rate_limit_buckets (bucket_key, window_start, count, updated_at)
                VALUES (:bucket_key, :window_start, 1, :updated_at)
                ON CONFLICT (bucket_key) DO UPDATE SET
                    count = CASE
                        WHEN rate_limit_buckets.window_start = :window_start
                        THEN rate_limit_buckets.count + 1
                        ELSE 1
                    END,
                    window_start = :window_start,
                    updated_at = :updated_at
                RETURNING count
                """
            ),
            {
                "bucket_key": k,
                "window_start": window_start,
                "updated_at": updated_at,
            },
        ).first()
        db.commit()

    count = int(row[0]) if row is not None else 1
    if count > limit:
        retry_at = datetime.fromtimestamp(window_start + _WINDOW_SECONDS, tz=UTC)
        retry_after = max(1, int((retry_at - updated_at).total_seconds()))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for {bucket}. Retry after {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )


def rate_limit(bucket: str):
    """Return a FastAPI dependency callable that rate-limits by account_id.

    Usage:
        @router.post("/endpoint")
        def handler(
            _: None = Depends(rate_limit("pipeline")),
            auth: tuple[str, bytes | None] = Depends(require_account),
        ):
            ...
    """

    def _depends(auth: tuple[str, bytes | None] = Depends(require_account)) -> None:
        account_id = auth[0]
        _check_and_increment(account_id, bucket)

    return _depends
