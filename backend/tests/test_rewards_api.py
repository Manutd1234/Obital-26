from __future__ import annotations

import unittest
from contextlib import contextmanager
from dataclasses import dataclass
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.api.common import require_account
from backend.app.services.crypto_local import decrypt_text
from backend.app.services.models import RewardEventModel, RewardSessionModel
from backend.app.services.storage import append_reward_session_log, get_rewards_dashboard


def _mock_require_account():
    return ("admin", None)


class _FakeRewardQuery:
    def __init__(self, row: RewardSessionModel | None) -> None:
        self.row = row

    def where(self, *args, **kwargs) -> "_FakeRewardQuery":
        return self

    def first(self) -> RewardSessionModel | None:
        return self.row


@dataclass
class _FakeRewardDb:
    row: RewardSessionModel | None
    added: list[object]

    def query(self, model) -> _FakeRewardQuery:
        return _FakeRewardQuery(self.row)

    def add(self, row: object) -> None:
        self.added.append(row)

    def commit(self) -> None:
        return None


class _FakeRewardSessionFactory:
    def __init__(self, db: _FakeRewardDb) -> None:
        self.db = db

    @contextmanager
    def __call__(self):
        yield self.db


class _FakeDashboardStore:
    def __init__(self, user: dict) -> None:
        self.user = user

    def get_user(self, account_id: str) -> dict:
        return self.user

    def build_dashboard_data(self, *, username: str, limit: int = 20) -> dict:
        return {
            "username": username,
            "summary": {
                "total_points": 10,
                "total_quizzes": 1,
                "total_correct": 1,
                "total_answered": 1,
                "accuracy_percent": 100.0,
                "updated_at": "2026-06-11T00:00:00Z",
            },
            "trend": [],
        }


class RewardsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[require_account] = _mock_require_account
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(require_account, None)

    def test_dashboard_returns_payload(self) -> None:
        payload = {
            "username": "admin",
            "summary": {
                "total_points": 10,
                "total_quizzes": 1,
                "total_correct": 1,
                "total_answered": 1,
                "accuracy_percent": 100.0,
                "updated_at": "2026-06-01T00:00:00Z",
            },
            "trend": [],
            "session_logs": [],
        }
        with mock.patch("backend.app.api.rewards.get_rewards_dashboard", return_value=payload):
            resp = self.client.get("/rewards/dashboard", params={"account_id": "admin"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["summary"]["total_points"], 10)

    def test_start_reward_session_returns_id(self) -> None:
        with mock.patch("backend.app.api.rewards.start_reward_session_log", return_value="reward_session_abc"):
            resp = self.client.post("/rewards/sessions", params={"account_id": "admin"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["session_id"], "reward_session_abc")

    def test_dashboard_unknown_account_returns_empty_defaults(self) -> None:
        with mock.patch("backend.app.api.rewards.get_rewards_dashboard", return_value={
            "username": "admin",
            "summary": {"total_points": 0, "total_quizzes": 0, "total_correct": 0, "total_answered": 0, "accuracy_percent": 0.0, "updated_at": None},
            "trend": [],
            "session_logs": [],
        }):
            resp = self.client.get("/rewards/dashboard", params={"account_id": "admin"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["summary"]["total_points"], 0)
        self.assertEqual(body["trend"], [])

    def test_append_event_returns_404_when_session_missing(self) -> None:
        with mock.patch("backend.app.api.rewards.append_reward_session_log", return_value=False):
            resp = self.client.post("/rewards/sessions/missing/events", params={"account_id": "admin"})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"], "Reward session log not found")

    def test_dashboard_merges_reward_logs_with_quiz_history_days(self) -> None:
        user = {
            "quiz_history": [
                {
                    "quiz_id": "quiz_old",
                    "submitted_at": "2026-06-10T12:00:00Z",
                    "points_earned": 25,
                },
                {
                    "quiz_id": "quiz_same_day",
                    "submitted_at": "2026-06-11T12:00:00Z",
                    "points_earned": 35,
                },
            ]
        }
        reward_logs = [
            {
                "session_id": "reward_session_1",
                "started_at": "2026-06-11T09:00:00Z",
                "last_event_at": "2026-06-11T10:00:00Z",
                "points_delta": 10,
                "quizzes_delta": 1,
                "events": 2,
            }
        ]

        with mock.patch("backend.app.services.storage._account_store", return_value=_FakeDashboardStore(user)):
            with mock.patch("backend.app.services.storage.list_reward_session_logs", return_value=reward_logs):
                payload = get_rewards_dashboard(account_id="admin", limit=20)

        logs_by_day = {log["last_event_at"][:10]: log for log in payload["session_logs"]}
        self.assertIn("2026-06-10", logs_by_day)
        self.assertIn("2026-06-11", logs_by_day)
        self.assertEqual(logs_by_day["2026-06-10"]["points_delta"], 25)
        self.assertEqual(logs_by_day["2026-06-11"]["points_delta"], 35)
        self.assertEqual(logs_by_day["2026-06-11"]["quizzes_delta"], 1)

    def test_reward_note_is_encrypted_at_rest_in_db_mode(self) -> None:
        fake_db = _FakeRewardDb(row=RewardSessionModel(session_id="reward_session_1", account_id="admin"), added=[])
        dek = b"k" * 32
        with mock.patch("backend.app.services.storage.db_enabled", return_value=True):
            with mock.patch("backend.app.services.storage.is_sqlite_mode", return_value=False):
                with mock.patch("backend.app.services.storage.SessionLocal", new=_FakeRewardSessionFactory(fake_db)):
                    with mock.patch("backend.app.services.storage._safe_rewards_summary", return_value={"total_points": 1}):
                        ok = append_reward_session_log(
                            account_id="admin",
                            session_id="reward_session_1",
                            note="sensitive reward note",
                            user_dek=dek,
                        )

        self.assertTrue(ok)
        event_rows = [row for row in fake_db.added if isinstance(row, RewardEventModel)]
        self.assertEqual(len(event_rows), 1)
        self.assertNotEqual(event_rows[0].note, "sensitive reward note")
        self.assertEqual(decrypt_text(dek, event_rows[0].note), "sensitive reward note")


if __name__ == "__main__":
    unittest.main()
