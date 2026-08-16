"""Background daemon that permanently deletes expired bin items every hour."""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

_CLEANUP_INTERVAL_SECONDS = int(os.getenv("BIN_CLEANUP_INTERVAL_SECONDS", "3600") or "3600")
_cleanup_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _cleanup_loop() -> None:
    from backend.app.services.db import db_enabled
    from backend.app.services.storage import permanently_delete_expired

    logger.info("Bin cleanup daemon started (interval=%ds)", _CLEANUP_INTERVAL_SECONDS)

    while not _stop_event.is_set():
        _stop_event.wait(timeout=_CLEANUP_INTERVAL_SECONDS)
        if _stop_event.is_set():
            break
        if not db_enabled():
            continue
        try:
            count = permanently_delete_expired()
            if count > 0:
                logger.info("Bin cleanup: permanently deleted %d expired items", count)
        except Exception as exc:
            logger.exception("Bin cleanup failed: %s", exc)

    logger.info("Bin cleanup daemon stopped")


def start_bin_cleanup() -> None:
    from backend.app.services.db import db_enabled
    global _cleanup_thread
    if not db_enabled():
        return
    if _cleanup_thread is not None and _cleanup_thread.is_alive():
        return
    _stop_event.clear()
    _cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True, name="bin-cleanup")
    _cleanup_thread.start()


def stop_bin_cleanup() -> None:
    _stop_event.set()
    if _cleanup_thread is not None:
        _cleanup_thread.join(timeout=5)
