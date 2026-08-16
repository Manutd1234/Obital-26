from fastapi import APIRouter, Depends, Query

from backend.app.api.common import require_account
from backend.app.schemas import LeaderboardResponse
from backend.app.services.storage import get_leaderboard

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.get("", response_model=LeaderboardResponse)
def leaderboard(
    auth: tuple[str, bytes | None] = Depends(require_account),
    limit: int = Query(50, ge=1, le=200),
) -> LeaderboardResponse:
    account_id, _user_dek = auth
    payload = get_leaderboard(account_id=account_id, limit=limit, user_dek=_user_dek)
    return LeaderboardResponse.model_validate(payload)
