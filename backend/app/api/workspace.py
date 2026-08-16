from fastapi import APIRouter, Depends

from backend.app.api.common import require_account
from backend.app.schemas import WorkspaceData
from backend.app.services.workspace_service import get_workspace, save_workspace

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get("", response_model=WorkspaceData)
def read_workspace(auth: tuple[str, bytes | None] = Depends(require_account)) -> WorkspaceData:
    account_id, user_dek = auth
    return get_workspace(account_id=account_id, user_dek=user_dek)


@router.put("", response_model=WorkspaceData)
def write_workspace(
    payload: WorkspaceData,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> WorkspaceData:
    account_id, user_dek = auth
    return save_workspace(account_id=account_id, data=payload, user_dek=user_dek)
