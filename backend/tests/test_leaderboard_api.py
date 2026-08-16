from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.api.common import require_account
from engine.quiz.account_system import AccountStore


def _mock_require_account():
    return ("alice", None)


class LeaderboardApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[require_account] = _mock_require_account
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(require_account, None)

    def test_leaderboard_returns_ranked_payload(self) -> None:
        payload = {
            "entries": [
                {"rank": 1, "account_id": "bob", "display_name": "bob", "total_points": 120, "total_quizzes": 10, "accuracy_percent": 90.0},
                {"rank": 2, "account_id": "alice", "display_name": "alice", "total_points": 80, "total_quizzes": 8, "accuracy_percent": 75.0},
            ],
            "total_users": 2,
            "me": {"rank": 2, "account_id": "alice", "display_name": "alice", "total_points": 80, "total_quizzes": 8, "accuracy_percent": 75.0},
        }
        with mock.patch("backend.app.api.leaderboard.get_leaderboard", return_value=payload) as patched:
            resp = self.client.get("/leaderboard", params={"account_id": "alice", "limit": 10})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total_users"], 2)
        self.assertEqual([e["rank"] for e in body["entries"]], [1, 2])
        self.assertEqual(body["entries"][0]["account_id"], "bob")  # highest XP first
        self.assertEqual(body["me"]["rank"], 2)
        # caller's account_id is forwarded so the service can mark "me"
        self.assertEqual(patched.call_args.kwargs["account_id"], "alice")

    def test_limit_is_validated(self) -> None:
        # limit above the allowed ceiling (200) is rejected by the Query constraint.
        resp = self.client.get("/leaderboard", params={"account_id": "alice", "limit": 9999})
        self.assertEqual(resp.status_code, 422)


class ListLeaderboardLogicTests(unittest.TestCase):
    """Exercises AccountStore.list_leaderboard ranking against a local accounts file."""

    def _store_with_users(self, users: dict) -> AccountStore:
        tmp = Path(tempfile.mkdtemp()) / "accounts.json"
        tmp.write_text(json.dumps({"users": users}), encoding="utf-8")
        return AccountStore(tmp)

    def test_ranking_order_and_accuracy(self) -> None:
        users = {
            "alice": {"username": "alice", "total_points": 50, "total_quizzes": 5, "total_correct": 8, "total_answered": 10},
            "bob": {"username": "bob", "total_points": 120, "total_quizzes": 9, "total_correct": 18, "total_answered": 20},
            "carol": {"username": "carol", "total_points": 0, "total_quizzes": 0, "total_correct": 0, "total_answered": 0},
        }
        store = self._store_with_users(users)
        with mock.patch("engine.quiz.account_system._is_db_storage_mode", return_value=False):
            ranked = store.list_leaderboard()

        self.assertEqual([e["account_id"] for e in ranked], ["bob", "alice", "carol"])
        self.assertEqual([e["rank"] for e in ranked], [1, 2, 3])
        self.assertEqual(ranked[0]["total_points"], 120)
        self.assertEqual(ranked[1]["accuracy_percent"], 80.0)
        # divide-by-zero is guarded
        self.assertEqual(ranked[2]["accuracy_percent"], 0.0)

    def test_tiebreak_is_deterministic(self) -> None:
        users = {
            "zeta": {"username": "zeta", "total_points": 100, "total_quizzes": 5},
            "alpha": {"username": "alpha", "total_points": 100, "total_quizzes": 5},
        }
        store = self._store_with_users(users)
        with mock.patch("engine.quiz.account_system._is_db_storage_mode", return_value=False):
            ranked = store.list_leaderboard()
        # equal points + quizzes → account_id ascending
        self.assertEqual([e["account_id"] for e in ranked], ["alpha", "zeta"])

    def test_reconcile_counters_matches_dashboard_history(self) -> None:
        users = {
            "alice": {
                "username": "alice",
                "total_points": 999,
                "total_quizzes": 99,
                "total_correct": 99,
                "total_answered": 99,
                "quiz_history": [
                    {"points_earned": 12, "correct_answers": 2, "answered_questions": 3},
                    {"points_earned": 8, "correct_answers": 1, "answered_questions": 2},
                ],
            }
        }
        store = self._store_with_users(users)
        with mock.patch("engine.quiz.account_system._is_db_storage_mode", return_value=False):
            totals = store.reconcile_account_counters_from_history(username="alice")
            ranked = store.list_leaderboard()

        self.assertEqual(totals, {
            "total_points": 20,
            "total_quizzes": 2,
            "total_correct": 3,
            "total_answered": 5,
        })
        self.assertEqual(ranked[0]["total_points"], 20)
        self.assertEqual(ranked[0]["total_quizzes"], 2)
        self.assertEqual(ranked[0]["accuracy_percent"], 60.0)


if __name__ == "__main__":
    unittest.main()
