from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from backend.app.services.storage import CHAT_DIR

_STATE_PATH = CHAT_DIR / "harness_pending.json"
_LOCK = Lock()


def _load() -> dict:
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(payload: dict) -> None:
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def put_pending_action(
    *,
    session_id: str,
    account_id: str,
    tool_name: str,
    arguments: dict[str, object],
    label: str,
) -> dict[str, object]:
    action_id = f"act_{uuid.uuid4().hex[:12]}"
    action = {
        "action_id": action_id,
        "session_id": session_id,
        "account_id": account_id,
        "tool_name": tool_name,
        "arguments": arguments,
        "label": label,
        "created_at": datetime.now(UTC).isoformat(),
    }
    with _LOCK:
        payload = _load()
        payload[action_id] = action
        _save(payload)
    return action


def pop_pending_action(*, action_id: str, session_id: str, account_id: str) -> dict[str, object] | None:
    with _LOCK:
        payload = _load()
        action = payload.get(action_id)
        if not isinstance(action, dict):
            return None
        if action.get("session_id") != session_id or action.get("account_id") != account_id:
            return None
        payload.pop(action_id, None)
        _save(payload)
        return action


def get_pending_action(*, action_id: str, session_id: str, account_id: str) -> dict[str, object] | None:
    with _LOCK:
        action = _load().get(action_id)
    if not isinstance(action, dict):
        return None
    if action.get("session_id") != session_id or action.get("account_id") != account_id:
        return None
    return action
