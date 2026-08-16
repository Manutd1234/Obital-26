from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.app.api.common import require_account
from backend.app.schemas import OwnedPack, PurchaseRequest, ShopState
from backend.app.services.shop_service import (
    ShopError,
    ShopNotReady,
    grade_pack,
    get_shop,
    mark_opened,
    purchase_pack,
)
from engine.quiz.quiz_backend import QuizGradeResponse, QuizSubmissionAnswer

router = APIRouter(prefix="/shop", tags=["shop"])


class GradePackRequest(BaseModel):
    answers: list[QuizSubmissionAnswer]


@router.get("", response_model=ShopState)
def read_shop(auth: tuple[str, bytes | None] = Depends(require_account)) -> ShopState:
    account_id, user_dek = auth
    return ShopState.model_validate(get_shop(account_id=account_id, user_dek=user_dek))


@router.post("/purchase", response_model=OwnedPack)
async def purchase(
    payload: PurchaseRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> OwnedPack:
    account_id, user_dek = auth
    try:
        pack = await purchase_pack(account_id=account_id, tier=payload.tier, user_dek=user_dek)
    except ShopNotReady as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ShopError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OwnedPack.model_validate(pack)


@router.post("/packs/{pack_id}/open", response_model=OwnedPack)
def open_pack(
    pack_id: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> OwnedPack:
    account_id, user_dek = auth
    pack = mark_opened(account_id=account_id, pack_id=pack_id, user_dek=user_dek)
    if pack is None:
        raise HTTPException(status_code=404, detail="Pack not found")
    return OwnedPack.model_validate(pack)


@router.post("/packs/{pack_id}/grade", response_model=QuizGradeResponse)
def grade_owned_pack(
    pack_id: str,
    payload: GradePackRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> QuizGradeResponse:
    account_id, user_dek = auth
    try:
        return grade_pack(account_id=account_id, pack_id=pack_id, answers=payload.answers, user_dek=user_dek)
    except ShopError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
