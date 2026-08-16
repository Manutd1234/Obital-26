from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from backend.app.api.common import require_account
from engine.quiz.account_system import AccountStore
from pathlib import Path

router = APIRouter(prefix="/presence", tags=["presence"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

_stored: AccountStore | None = None


def _store() -> AccountStore:
    global _stored
    if _stored is None:
        _stored = AccountStore(BASE_DIR / "engine" / "accounts" / "accounts.json")
    return _stored


class HeartbeatRequest(BaseModel):
    status: str = "online"


@router.post("/heartbeat")
def heartbeat(
    body: HeartbeatRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    account_id, _dek = auth
    store = _store()
    store.update_presence(username=account_id, status=body.status)
    return {"status": "ok"}
