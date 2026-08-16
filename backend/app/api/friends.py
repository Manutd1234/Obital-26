from fastapi import APIRouter, Depends, HTTPException
from backend.app.api.common import require_account, require_account_id, handle_user_error
from backend.app.schemas import FriendInfo, FriendSearchResult, FriendPresence
from engine.quiz.account_system import AccountStore
from engine.shared.account_paths import normalize_account_id
from pathlib import Path

router = APIRouter(prefix="/friends", tags=["friends"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

_stored: AccountStore | None = None


def _store() -> AccountStore:
    global _stored
    if _stored is None:
        _stored = AccountStore(BASE_DIR / "engine" / "accounts" / "accounts.json")
    return _stored


@router.get("")
def list_friends(
    account_id: str = Depends(require_account_id),
) -> list[FriendInfo]:
    store = _store()
    friends = store.get_friends(username=account_id)
    return [
        FriendInfo(
            account_id=f.get("account_id", ""),
            display_name=f.get("display_name", f.get("account_id", "")),
            added_at=f.get("added_at", ""),
        )
        for f in friends
        if isinstance(f, dict)
    ]


@router.get("/search")
def search_friend(
    q: str,
    account_id: str = Depends(require_account_id),
) -> FriendSearchResult:
    store = _store()
    target = normalize_account_id(q)
    if target == account_id:
        raise HTTPException(status_code=400, detail="Cannot add yourself")
    if not store.user_exists(target):
        raise HTTPException(status_code=404, detail="User not found")
    return FriendSearchResult(
        account_id=target,
        display_name=target,
        already_friend=store.is_friend(account_id, target),
    )


@router.post("/{friend_id}")
def add_friend(
    friend_id: str,
    account_id: str = Depends(require_account_id),
) -> FriendInfo:
    store = _store()
    try:
        entry = store.add_friend(username=account_id, friend_id=friend_id)
        return FriendInfo(
            account_id=entry.get("account_id", ""),
            display_name=entry.get("display_name", entry.get("account_id", "")),
            added_at=entry.get("added_at", ""),
        )
    except RuntimeError as exc:
        raise handle_user_error(exc, not_found="not found" in str(exc).lower())


@router.delete("/{friend_id}")
def remove_friend(
    friend_id: str,
    account_id: str = Depends(require_account_id),
) -> dict:
    store = _store()
    try:
        store.remove_friend(username=account_id, friend_id=friend_id)
        return {"status": "ok"}
    except RuntimeError as exc:
        raise handle_user_error(exc)


@router.get("/presence")
def get_friends_presence(
    account_id: str = Depends(require_account_id),
) -> list[FriendPresence]:
    store = _store()
    items = store.get_friends_presence(username=account_id)
    return [
        FriendPresence(
            account_id=item.get("account_id", ""),
            display_name=item.get("display_name", ""),
            status=item.get("status", "offline"),
            online=bool(item.get("online", False)),
            last_seen_at=item.get("last_seen_at"),
        )
        for item in items
        if isinstance(item, dict)
    ]
