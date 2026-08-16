from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from pathlib import Path

from engine.quiz.quiz_backend import (
    QuizBuildResponse,
    QuizGradeRequest,
    QuizGradeResponse,
    QuizSubmissionAnswer,
    grade_quiz,
)
from engine.shared.account_paths import account_root

from backend.app.services.settings_service import (
    _is_db_storage_mode,
    _load_json,
    _read_artifact,
    _resolve_dek,
    _save_json,
    _upsert_artifact,
)

BASE_DIR = Path(__file__).resolve().parents[3]
SHOP_RELATIVE_PATH = "shop/shop.json"
logger = logging.getLogger(__name__)

# Pack catalogue: tier -> difficulty, number of quiz "cards", XP cost.
TIERS: dict[str, dict] = {
    "silver": {"label": "Silver", "difficulty": "easy", "num_questions": 3, "cost": 50},
    "gold": {"label": "Gold", "difficulty": "medium", "num_questions": 5, "cost": 150},
    "platinum": {"label": "Platinum", "difficulty": "hard", "num_questions": 7, "cost": 300},
}

class ShopError(ValueError):
    """User-facing 400 (bad request / business rule)."""


class ShopNotReady(RuntimeError):
    """User-facing 503 (documents still being prepared)."""


def _default_shop() -> dict:
    return {"spent_points": 0, "packs": []}


def _account_shop_path(account_id: str) -> Path:
    return account_root(base_dir=BASE_DIR, account_id=account_id) / SHOP_RELATIVE_PATH


def _read_shop(account_id: str, user_dek: bytes | None) -> dict:
    if _is_db_storage_mode():
        from backend.app.services.db import SessionLocal

        dek = _resolve_dek(user_dek)
        assert SessionLocal is not None
        with SessionLocal() as db:
            doc = _read_artifact(db, account_id=account_id, relative_path=SHOP_RELATIVE_PATH, dek=dek)
        data = doc if isinstance(doc, dict) else _default_shop()
    else:
        data = _load_json(_account_shop_path(account_id)) or _default_shop()

    merged = {**_default_shop(), **data}
    merged["spent_points"] = int(merged.get("spent_points", 0) or 0)
    if not isinstance(merged.get("packs"), list):
        merged["packs"] = []
    return merged


def _write_shop(account_id: str, data: dict, user_dek: bytes | None) -> None:
    if _is_db_storage_mode():
        from backend.app.services.crypto_local import require_dek
        from backend.app.services.db import SessionLocal, is_sqlite_mode

        dek = _resolve_dek(user_dek)
        if not is_sqlite_mode():
            require_dek(dek, "shop")
        assert SessionLocal is not None
        content_bytes = json.dumps(data).encode("utf-8")
        with SessionLocal() as db:
            _upsert_artifact(
                db, account_id=account_id, relative_path=SHOP_RELATIVE_PATH, content_bytes=content_bytes, dek=dek
            )
            db.commit()
    else:
        _save_json(_account_shop_path(account_id), data)


def _earned_xp(account_id: str, user_dek: bytes | None = None) -> int:
    """Lifetime earned XP (the same stored counter the leaderboard ranks by)."""
    from backend.app.services.storage import _account_store

    user = _account_store().get_user(account_id, user_dek=user_dek)
    if not user:
        return 0
    return int(user.get("total_points", 0) or 0)


def _tiers_catalogue() -> list[dict]:
    return [
        {
            "id": tier_id,
            "label": spec["label"],
            "difficulty": spec["difficulty"],
            "num_questions": spec["num_questions"],
            "cost": spec["cost"],
        }
        for tier_id, spec in TIERS.items()
    ]


def get_shop(account_id: str, user_dek: bytes | None = None) -> dict:
    shop = _read_shop(account_id, user_dek)
    earned = _earned_xp(account_id, user_dek)
    spent = int(shop.get("spent_points", 0))
    return {
        "tiers": _tiers_catalogue(),
        "earned_xp": earned,
        "spent_points": spent,
        "available_xp": max(0, earned - spent),
        "packs": shop.get("packs", []),
    }


def _collect_pack_context(account_id: str, user_dek: bytes | None, *, max_chunks: int = 12) -> tuple[str, list[dict[str, str]]]:
    from backend.app.services.storage import list_docs

    docs = list_docs(account_id=account_id, user_dek=user_dek)
    contexts: list[dict[str, str]] = []
    doc_names: list[str] = []
    for doc in docs:
        doc_names.append(doc.name)
        for chunk in doc.chunks or []:
            text = str(chunk.text or "").strip()
            if not text:
                continue
            contexts.append({"doc": doc.name, "chunk_id": chunk.chunk_id, "text": text[:1500]})
            if len(contexts) >= max_chunks:
                break
        if len(contexts) >= max_chunks:
            break
    topic = "uploaded documents"
    if doc_names:
        topic = f"uploaded documents: {', '.join(doc_names[:5])}"
    return topic, contexts


async def purchase_pack(account_id: str, tier: str, user_dek: bytes | None = None) -> dict:
    """Buy a pack: validate -> harness quiz generation -> deduct -> store.

    Ordered so XP is only deducted once a pack can actually be built (no refunds needed).
    """
    tier = (tier or "").strip().lower()
    if tier not in TIERS:
        raise ShopError(f"Unknown pack tier: {tier!r}")
    spec = TIERS[tier]

    # 1. Require uploaded documents (packs are generated from them).
    from backend.app.services.storage import list_docs

    docs = list_docs(account_id=account_id, user_dek=user_dek, include_content=False)
    if not docs:
        raise ShopError("Upload at least one document before buying a pack.")

    # 2. Balance pre-check (fail fast before any slow work).
    shop = _read_shop(account_id, user_dek)
    earned = _earned_xp(account_id, user_dek)
    spent = int(shop.get("spent_points", 0))
    available = max(0, earned - spent)
    if available < spec["cost"]:
        raise ShopError(f"Not enough XP: this pack costs {spec['cost']}, you have {available} available.")

    # 3. Generate document-grounded quiz cards through the quiz harness.
    topic, contexts = _collect_pack_context(account_id, user_dek)
    if not contexts:
        raise ShopError("Your documents do not have readable quiz material yet. Try re-uploading or adding text.")
    from backend.app.services.quiz_harness import generate_quiz

    quiz = await generate_quiz(
        topic=topic,
        num_questions=spec["num_questions"],
        question_mode="mcq",
        difficulty=spec["difficulty"],
        contexts=contexts,
        context_source="documents",
    )
    if quiz.build_indicator == "failed" or not quiz.questions:
        raise ShopError(
            f"Couldn't build a {spec['label']} pack from your documents "
            f"(not enough {spec['difficulty']} material). Try another tier or upload more docs."
        )

    # 5. Deduct + store the pack.
    pack_id = uuid.uuid4().hex
    quiz.quiz_id = f"shop_pack_{pack_id}"
    pack = {
        "id": pack_id,
        "tier": tier,
        "difficulty": spec["difficulty"],
        "cost": spec["cost"],
        "created_at": datetime.now(UTC).isoformat(),
        "opened": False,
        "rewarded_at": None,
        "best_score_percent": None,
        "quiz": quiz.model_dump(),
    }
    shop["spent_points"] = spent + spec["cost"]
    shop.setdefault("packs", []).append(pack)
    _write_shop(account_id, shop, user_dek)
    return pack


def mark_opened(account_id: str, pack_id: str, user_dek: bytes | None = None) -> dict | None:
    shop = _read_shop(account_id, user_dek)
    found = None
    for pack in shop.get("packs", []):
        if pack.get("id") == pack_id:
            pack["opened"] = True
            found = pack
    if found is not None:
        _write_shop(account_id, shop, user_dek)
    return found


def grade_pack(
    *,
    account_id: str,
    pack_id: str,
    answers: list[QuizSubmissionAnswer],
    user_dek: bytes | None = None,
) -> QuizGradeResponse:
    shop = _read_shop(account_id, user_dek)
    pack: dict[str, Any] | None = None
    for candidate in shop.get("packs", []):
        if isinstance(candidate, dict) and candidate.get("id") == pack_id:
            pack = candidate
            break
    if pack is None:
        raise ShopError("Pack not found.")

    quiz = QuizBuildResponse(**(pack.get("quiz") or {}))
    result = grade_quiz(quiz=quiz, request=QuizGradeRequest(quiz_id=quiz.quiz_id, answers=answers))
    best = pack.get("best_score_percent")
    try:
        previous_best = float(best) if best is not None else None
    except (TypeError, ValueError):
        previous_best = None
    if previous_best is None or result.score_percent > previous_best:
        pack["best_score_percent"] = float(result.score_percent)

    if not pack.get("rewarded_at"):
        from backend.app.services.storage import _account_store

        _account_store().apply_quiz_result(
            username=account_id, result=result, quiz=quiz, user_dek=user_dek
        )
        pack["rewarded_at"] = datetime.now(UTC).isoformat()
    _write_shop(account_id, shop, user_dek)
    return result
