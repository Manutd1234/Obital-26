from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.api.common import require_account
from backend.app.schemas import RewardsDashboardResponse, StartRewardSessionResponse
from backend.app.services.storage import append_reward_session_log, get_rewards_dashboard, start_reward_session_log

router = APIRouter(prefix="/rewards", tags=["rewards"])


@router.get("/dashboard", response_model=RewardsDashboardResponse)
def rewards_dashboard(
    auth: tuple[str, bytes | None] = Depends(require_account),
    limit: int = Query(20, ge=1, le=100),
) -> RewardsDashboardResponse:
    account_id, _user_dek = auth
    try:
        payload = get_rewards_dashboard(account_id=account_id, limit=limit, user_dek=_user_dek)
    except RuntimeError as exc:
        from backend.app.api.common import handle_user_error
        raise handle_user_error(exc) from exc
    return RewardsDashboardResponse.model_validate(payload)


@router.post("/sessions", response_model=StartRewardSessionResponse)
def start_reward_session(auth: tuple[str, bytes | None] = Depends(require_account)) -> StartRewardSessionResponse:
    account_id, _user_dek = auth
    return StartRewardSessionResponse(session_id=start_reward_session_log(account_id=account_id))


@router.post("/sessions/{session_id}/events")
def append_reward_event(
    session_id: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
    note: str = "",
) -> dict:
    account_id, user_dek = auth
    ok = append_reward_session_log(account_id=account_id, session_id=session_id, note=note, user_dek=user_dek)
    if not ok:
        raise HTTPException(status_code=404, detail="Reward session log not found")
    return {"status": "ok"}
