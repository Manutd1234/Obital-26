from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pathlib import Path

from backend.app.api.common import require_account
from backend.app.schemas import BinItem
from backend.app.services.storage import list_bin_items, restore_doc, permanent_delete_bin_item
from engine.quiz.account_system import AccountStore

router = APIRouter(prefix="/bin", tags=["bin"])

_BASE_DIR = Path(__file__).resolve().parents[3]


def _account_store() -> AccountStore:
    return AccountStore(_BASE_DIR / "engine" / "accounts" / "accounts.json")


@router.get("", response_model=list[BinItem])
def get_bin(
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> list[BinItem]:
    """List all items in the bin for the current account."""
    account_id, user_dek = auth
    items = list_bin_items(account_id=account_id)
    return [BinItem(**item) for item in items]


@router.post("/{bin_id}/restore")
def restore_bin_item(
    bin_id: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    """Restore an item from the bin."""
    from backend.app.services.db import SessionLocal, db_enabled
    from backend.app.services.models import BinItemModel

    account_id, user_dek = auth

    if not db_enabled():
        raise HTTPException(status_code=503, detail="Database required for bin operations")

    assert SessionLocal is not None
    with SessionLocal() as db:
        bin_row = db.query(BinItemModel).where(
            BinItemModel.id == bin_id,
            BinItemModel.account_id == account_id,
        ).first()
        if bin_row is None:
            raise HTTPException(status_code=404, detail="Bin item not found")

        item_type = bin_row.item_type
        item_id = bin_row.item_id
        metadata = json.loads(bin_row.metadata_json)

        if item_type == "document":
            success = restore_doc(bin_id, account_id=account_id, user_dek=user_dek)
            if not success:
                raise HTTPException(status_code=404, detail="Document data not found")
            return {"status": "restored", "item_type": item_type, "item_id": item_id}

        if item_type == "lesson":
            state = metadata.get("state")
            if not state:
                db.delete(bin_row)
                db.commit()
                raise HTTPException(status_code=400, detail="Invalid lesson bin entry")
            store = _account_store()
            try:
                store.restore_lesson(username=account_id, lesson_state=state)
            except RuntimeError as exc:
                db.delete(bin_row)
                db.commit()
                raise HTTPException(status_code=400, detail=str(exc))
            db.delete(bin_row)
            db.commit()
            return {"status": "restored", "item_type": item_type, "item_id": item_id}

        db.delete(bin_row)
        db.commit()
        return {"status": "restored", "item_type": item_type, "item_id": item_id}


@router.delete("/{bin_id}")
def permanent_delete(
    bin_id: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    """Permanently delete an item from the bin."""
    account_id, user_dek = auth
    success = permanent_delete_bin_item(bin_id, account_id=account_id)
    if not success:
        raise HTTPException(status_code=404, detail="Bin item not found")
    return {"status": "permanently_deleted", "bin_id": bin_id}
