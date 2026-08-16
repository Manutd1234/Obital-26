from fastapi import APIRouter, Depends, HTTPException

from backend.app.api.common import require_account
from backend.app.schemas import SettingsPatchRequest, SettingsResponse
from backend.app.services.settings_service import get_settings, update_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SettingsResponse)
def get_account_settings(
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> SettingsResponse:
    account_id, user_dek = auth
    return get_settings(account_id=account_id, user_dek=user_dek)


@router.patch("", response_model=SettingsResponse)
def patch_account_settings(
    payload: SettingsPatchRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> SettingsResponse:
    account_id, user_dek = auth
    try:
        return update_settings(account_id=account_id, patch=payload, user_dek=user_dek)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
