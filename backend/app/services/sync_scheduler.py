"""Background sync scheduler for periodic cloud push/pull.

When both a local SQLite database and a cloud (Postgres) database are configured,
this module runs periodic sync tasks to keep them in sync.

Configure via environment variables:
    LOCAL_DATABASE_URL   - Local SQLite path (e.g. sqlite:///backend/data/obital.db)
    DATABASE_URL         - Remote Postgres URL (cloud sync target)
    SYNC_INTERVAL_MINUTES - How often to sync (0 = disabled, default 0)

Usage:
    The scheduler is started automatically in the FastAPI lifespan if configured.
    Manual sync can be triggered via POST /sync/push and POST /sync/pull endpoints.
"""

from __future__ import annotations

import logging
import os
import random
import threading
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "0") or "0")
if _SYNC_INTERVAL_MINUTES < 0:
    logger.warning("SYNC_INTERVAL_MINUTES was negative (%d), defaulting to 15", _SYNC_INTERVAL_MINUTES)
    _SYNC_INTERVAL_MINUTES = 15
_sync_thread: threading.Thread | None = None
_stop_event = threading.Event()


def sync_enabled() -> bool:
    return _SYNC_INTERVAL_MINUTES > 0


def _sync_loop() -> None:
    """Background thread that periodically syncs all accounts."""
    from backend.app.services.db import is_sqlite_mode, cloud_db_enabled

    logger.info("Sync scheduler started (interval=%d min)", _SYNC_INTERVAL_MINUTES)

    # Immediate first sync cycle
    if is_sqlite_mode() and cloud_db_enabled():
        try:
            _run_sync_cycle()
        except Exception as exc:
            logger.exception("Initial sync cycle failed: %s", exc)

    while not _stop_event.is_set():
        interval = _SYNC_INTERVAL_MINUTES * 60
        jitter = random.uniform(0, min(30, interval * 0.1))
        _stop_event.wait(timeout=interval + jitter)
        if _stop_event.is_set():
            break

        if not is_sqlite_mode() or not cloud_db_enabled():
            continue

        try:
            _run_sync_cycle()
        except Exception as exc:
            logger.exception("Sync cycle failed: %s", exc)

    logger.info("Sync scheduler stopped")


def _run_sync_cycle() -> None:
    """Run one sync cycle for all accounts that have active unlock sessions.

    Order: pull first, then push. This ensures cloud data from other devices
    is merged locally before local data is pushed up, preventing data loss.
    """
    from backend.app.services.db import SessionLocal, EFFECTIVE_DATABASE_URL, DATABASE_URL
    from backend.app.services.models import AccountUnlockSessionModel
    from backend.app.services.account_sync import push_account_to_cloud, pull_account_from_cloud
    from backend.app.services.unlock_session import resolve_dek_for_account, cleanup_expired_unlock_sessions

    if EFFECTIVE_DATABASE_URL == DATABASE_URL:
        logger.debug("Postgres-only mode: skipping cloud sync (same DB)")
        return

    assert SessionLocal is not None
    with SessionLocal() as db:
        cleanup_expired_unlock_sessions(db)

        active_sessions = (
            db.query(AccountUnlockSessionModel)
            .where(AccountUnlockSessionModel.expires_at > datetime.now(UTC))
            .all()
        )

        if not active_sessions:
            logger.debug("No active unlock sessions found, skipping sync")
            return

        account_ids = {s.account_id for s in active_sessions}

        for account_id in account_ids:
            try:
                dek = resolve_dek_for_account(account_id, db)
                if dek is None:
                    logger.debug("Could not resolve DEK for %s, skipping", account_id)
                    continue

                logger.info("Syncing account %s", account_id)
                pull_report = pull_account_from_cloud(account_id, dek)
                push_report = push_account_to_cloud(account_id, dek)
                logger.info(
                    "Sync complete for %s: pull=%d chat, %d rewards, %d files; push=%d chat, %d rewards, %d files",
                    account_id,
                    pull_report.chat_sessions, pull_report.reward_sessions, pull_report.downloaded_files,
                    push_report.chat_sessions, push_report.reward_sessions, push_report.uploaded_files,
                )
            except Exception as exc:
                logger.exception("Failed syncing account %s: %s", account_id, exc)


def start_scheduler() -> None:
    """Start the background sync scheduler thread."""
    global _sync_thread
    if not sync_enabled():
        logger.info("Sync scheduler disabled (SYNC_INTERVAL_MINUTES=0)")
        return
    if _sync_thread is not None and _sync_thread.is_alive():
        return
    _stop_event.clear()
    _sync_thread = threading.Thread(target=_sync_loop, daemon=True, name="sync-scheduler")
    _sync_thread.start()


def stop_scheduler() -> None:
    """Stop the background sync scheduler thread."""
    _stop_event.set()
    if _sync_thread is not None:
        _sync_thread.join(timeout=5)
