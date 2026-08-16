from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.api.common import require_account
from backend.app.services import shop_service
from engine.quiz.quiz_backend import QuizBuildResponse, QuizQuestion


def _mock_require_account():
    return ("admin", None)


def _synthetic_quiz(diff: str = "easy", n: int = 3) -> QuizBuildResponse:
    questions = []
    for i in range(n):
        questions.append(
            QuizQuestion(
                question_id=f"q_{diff}_{i}",
                topic_id="topic_01",
                topic_title="Photosynthesis",
                question=f"{diff} question {i} about photosynthesis?",
                question_mode="mcq",
                options=["A", "B", "C", "D"],
                correct_option_index=0,
                expected_answer="A",
                explanation="Photosynthesis converts light into chemical energy.",
                difficulty=diff,
                source_context="Photosynthesis converts light into chemical energy.",
            )
        )
    return QuizBuildResponse(
        quiz_id=f"quiz_{diff}_{n}",
        topic_filter="uploaded documents",
        difficulty_filter=diff,
        questions=questions,
    )


class ShopApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[require_account] = _mock_require_account
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(require_account, None)

    def _patches(self, td: str, earned: int):
        return (
            mock.patch.object(shop_service, "BASE_DIR", Path(td)),
            mock.patch.object(shop_service, "_is_db_storage_mode", return_value=False),
            mock.patch.object(shop_service, "_earned_xp", return_value=earned),
            mock.patch("backend.app.services.storage.list_docs", return_value=[object()]),
            mock.patch.object(
                shop_service,
                "_collect_pack_context",
                return_value=("Photosynthesis", [{"doc": "bio.txt", "chunk_id": "c1", "text": "Photosynthesis"}]),
            ),
        )

    def test_get_shop_lists_tiers_and_balance(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = self._patches(td, earned=500)
            with p[0], p[1], p[2], p[3], p[4]:
                resp = self.client.get("/shop", params={"account_id": "admin"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual({t["id"] for t in body["tiers"]}, {"silver", "gold", "platinum"})
        self.assertEqual(body["earned_xp"], 500)
        self.assertEqual(body["available_xp"], 500)

    def test_purchase_builds_pack_and_deducts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = self._patches(td, earned=500)
            with p[0], p[1], p[2], p[3], p[4], mock.patch(
                "backend.app.services.quiz_harness.generate_quiz",
                new=mock.AsyncMock(return_value=_synthetic_quiz("easy", 3)),
            ) as generate:
                buy = self.client.post("/shop/purchase", params={"account_id": "admin"}, json={"tier": "silver"})
                self.assertEqual(buy.status_code, 200, buy.text)
                generate.assert_awaited_once()
                pack = buy.json()
                self.assertEqual(pack["tier"], "silver")
                self.assertEqual(pack["difficulty"], "easy")
                self.assertEqual(len(pack["quiz"]["questions"]), 3)
                # every card should be a silver/easy MCQ
                for q in pack["quiz"]["questions"]:
                    self.assertEqual(q["question_mode"], "mcq")

                state = self.client.get("/shop", params={"account_id": "admin"}).json()
                self.assertEqual(state["spent_points"], 50)
                self.assertEqual(state["available_xp"], 450)
                self.assertEqual(len(state["packs"]), 1)

    def test_purchase_platinum_has_seven_cards(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = self._patches(td, earned=1000)
            with p[0], p[1], p[2], p[3], p[4], mock.patch(
                "backend.app.services.quiz_harness.generate_quiz",
                new=mock.AsyncMock(return_value=_synthetic_quiz("hard", 7)),
            ):
                buy = self.client.post("/shop/purchase", params={"account_id": "admin"}, json={"tier": "platinum"})
        self.assertEqual(buy.status_code, 200, buy.text)
        self.assertEqual(len(buy.json()["quiz"]["questions"]), 7)

    def test_insufficient_xp_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = self._patches(td, earned=10)
            with p[0], p[1], p[2], p[3], p[4]:
                buy = self.client.post("/shop/purchase", params={"account_id": "admin"}, json={"tier": "gold"})
        self.assertEqual(buy.status_code, 400)
        self.assertIn("Not enough XP", buy.json()["detail"])

    def test_no_docs_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(shop_service, "BASE_DIR", Path(td)),
                mock.patch.object(shop_service, "_is_db_storage_mode", return_value=False),
                mock.patch.object(shop_service, "_earned_xp", return_value=500),
                mock.patch("backend.app.services.storage.list_docs", return_value=[]),
            ):
                buy = self.client.post("/shop/purchase", params={"account_id": "admin"}, json={"tier": "silver"})
        self.assertEqual(buy.status_code, 400)
        self.assertIn("Upload at least one document", buy.json()["detail"])

    def test_unknown_tier_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = self._patches(td, earned=500)
            with p[0], p[1], p[2], p[3], p[4]:
                buy = self.client.post("/shop/purchase", params={"account_id": "admin"}, json={"tier": "diamond"})
        self.assertEqual(buy.status_code, 400)


if __name__ == "__main__":
    unittest.main()
