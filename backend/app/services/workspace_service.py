from __future__ import annotations

import json
import logging
from pathlib import Path

from engine.shared.account_paths import account_root

from backend.app.schemas import WorkspaceData
from backend.app.services.settings_service import (
    _is_db_storage_mode,
    _load_json,
    _read_artifact,
    _resolve_dek,
    _save_json,
    _upsert_artifact,
)

BASE_DIR = Path(__file__).resolve().parents[3]
WORKSPACE_RELATIVE_PATH = "workspace/workspace.json"
logger = logging.getLogger(__name__)


def _default_workspace() -> dict:
    return {"code": {"js": "", "python": ""}, "tasks": {}, "sessions": []}


def _account_workspace_path(account_id: str) -> Path:
    return account_root(base_dir=BASE_DIR, account_id=account_id) / WORKSPACE_RELATIVE_PATH


def get_workspace(account_id: str, user_dek: bytes | None = None) -> WorkspaceData:
    """Return the account's workspace document (code, calendar tasks, pomodoro sessions).

    Stored as a single E2EE artifact (Postgres mode) or a local JSON file (sqlite/no-db mode),
    mirroring how settings_service persists per-account config.
    """
    if _is_db_storage_mode():
        from backend.app.services.db import SessionLocal

        dek = _resolve_dek(user_dek)
        assert SessionLocal is not None
        with SessionLocal() as db:
            doc = _read_artifact(
                db,
                account_id=account_id,
                relative_path=WORKSPACE_RELATIVE_PATH,
                dek=dek,
            )
        data = doc if isinstance(doc, dict) else _default_workspace()
    else:
        data = _load_json(_account_workspace_path(account_id)) or _default_workspace()

    # Merge over defaults so older/partial docs still validate; Pydantic fills nested gaps.
    return WorkspaceData.model_validate({**_default_workspace(), **data})


def save_workspace(account_id: str, data: WorkspaceData, user_dek: bytes | None = None) -> WorkspaceData:
    """Persist the full workspace document (replace semantics)."""
    payload = data.model_dump()

    if _is_db_storage_mode():
        from backend.app.services.crypto_local import require_dek
        from backend.app.services.db import SessionLocal, is_sqlite_mode

        dek = _resolve_dek(user_dek)
        if not is_sqlite_mode():
            require_dek(dek, "workspace")
        assert SessionLocal is not None

        content_bytes = json.dumps(payload).encode("utf-8")
        with SessionLocal() as db:
            _upsert_artifact(
                db,
                account_id=account_id,
                relative_path=WORKSPACE_RELATIVE_PATH,
                content_bytes=content_bytes,
                dek=dek,
            )
            db.commit()
    else:
        _save_json(_account_workspace_path(account_id), payload)

    return WorkspaceData.model_validate(payload)
