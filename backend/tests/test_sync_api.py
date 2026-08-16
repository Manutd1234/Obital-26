from __future__ import annotations

import os
import unittest
from dataclasses import dataclass, field
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.models import AccountKeyModel, AccountUnlockSessionModel
from backend.app.services.unlock_session import create_unlock_session
from backend.tests.helpers import make_account_row


class _FakeQuery:
    def __init__(self, row) -> None:
        self.row = row

    def where(self, *args, **kwargs) -> "_FakeQuery":
        return self

    def with_for_update(self) -> "_FakeQuery":
        return self

    def first(self):
        return self.row

    def all(self):
        return [self.row] if self.row is not None else []

    def order_by(self, *args):
        return self


@dataclass
class _FakeDb:
    account_key: AccountKeyModel | None = None
    unlock_sessions: list[AccountUnlockSessionModel] = field(default_factory=list)
    artifact_rows: list = field(default_factory=list)
    registry_row: object | None = None
    commits: int = 0
    _added: list = field(default_factory=list)

    def query(self, model):
        from backend.app.services.models import AccountArtifactModel, AccountRegistryRecordModel
        if model.__name__ == "AccountKeyModel":
            return _FakeQuery(self.account_key)
        if model.__name__ == "AccountUnlockSessionModel":
            return _FakeQuery(self.unlock_sessions[0] if self.unlock_sessions else None)
        if model.__name__ == "AccountArtifactModel":
            return _FakeQuery(self.artifact_rows[0] if self.artifact_rows else None)
        if model.__name__ == "AccountRegistryRecordModel":
            return _FakeQuery(self.registry_row)
        raise AssertionError(f"Unexpected model query: {model}")

    def add(self, row) -> None:
        self._added.append(row)

    def delete(self, row) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeSessionFactory:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    def __call__(self):
        return self._db


class SyncApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.secret_patch = mock.patch.dict(os.environ, {"UNLOCK_TOKEN_SECRET": "test-unlock-secret", "OBITAL_LOCAL_SINGLE_USER": ""}, clear=False)
        self.secret_patch.start()

    def tearDown(self) -> None:
        self.secret_patch.stop()

    @staticmethod
    def _request(user_agent: str = "test-agent-01", client_id: str = "test-client-001") -> "Request":
        from starlette.requests import Request
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/health",
                "headers": [
                    (b"user-agent", user_agent.encode("utf-8")),
                    (b"x-client-id", client_id.encode("utf-8")),
                ],
                "client": ("127.0.0.1", 12345),
            }
        )

    def _make_token(self, account_id: str = "admin", unlock_version: int = 1, client_id: str = "test-client-001") -> str:
        request = self._request(client_id=client_id)
        fake_db = _FakeDb(account_key=make_account_row())
        return create_unlock_session(
            account_id=account_id,
            dek=b"s" * 32,
            unlock_version=unlock_version,
            request=request,
            db=fake_db,
        )

    # ── Push tests ───────────────────────────────────────

    def test_sync_push_rejects_missing_unlock_token(self) -> None:
        row = make_account_row()
        fake_db = _FakeDb(account_key=row)
        fake_factory = _FakeSessionFactory(fake_db)

        with mock.patch("backend.app.api.common.db_enabled", return_value=True):
            with mock.patch("backend.app.api.common.SessionLocal", new=fake_factory):
                resp = self.client.post(
                    "/sync/push",
                    params={"account_id": "admin"},
                    headers={"X-Client-Id": "test-client-001"},
                )
        self.assertEqual(resp.status_code, 401)
        self.assertIn("X-Unlock-Token", resp.json()["detail"])

    def test_sync_push_rejects_invalid_unlock_token(self) -> None:
        row = make_account_row()
        fake_db = _FakeDb(account_key=row)
        fake_factory = _FakeSessionFactory(fake_db)

        with mock.patch("backend.app.api.common.db_enabled", return_value=True):
            with mock.patch("backend.app.api.common.SessionLocal", new=fake_factory):
                resp = self.client.post(
                    "/sync/push",
                    params={"account_id": "admin"},
                    headers={
                        "X-Client-Id": "test-client-001",
                        "X-Unlock-Token": "bad-token",
                    },
                )
        self.assertEqual(resp.status_code, 401)

    def test_sync_push_rejects_when_db_disabled(self) -> None:
        with mock.patch("backend.app.api.common.db_enabled", return_value=False):
            resp = self.client.post(
                "/sync/push",
                params={"account_id": "admin"},
                headers={
                    "X-Client-Id": "test-client-001",
                    "X-Unlock-Token": "some-token",
                },
            )
        self.assertEqual(resp.status_code, 503)
        self.assertIn("Database required", resp.json()["detail"])

    # ── Pull tests ───────────────────────────────────────

    def test_sync_pull_rejects_missing_unlock_token(self) -> None:
        row = make_account_row()
        fake_db = _FakeDb(account_key=row)
        fake_factory = _FakeSessionFactory(fake_db)

        with mock.patch("backend.app.api.common.db_enabled", return_value=True):
            with mock.patch("backend.app.api.common.SessionLocal", new=fake_factory):
                resp = self.client.post(
                    "/sync/pull",
                    params={"account_id": "admin"},
                    headers={"X-Client-Id": "test-client-001"},
                )
        self.assertEqual(resp.status_code, 401)

    def test_sync_pull_rejects_invalid_unlock_token(self) -> None:
        row = make_account_row()
        fake_db = _FakeDb(account_key=row)
        fake_factory = _FakeSessionFactory(fake_db)

        with mock.patch("backend.app.api.common.db_enabled", return_value=True):
            with mock.patch("backend.app.api.common.SessionLocal", new=fake_factory):
                resp = self.client.post(
                    "/sync/pull",
                    params={"account_id": "admin"},
                    headers={
                        "X-Client-Id": "test-client-001",
                        "X-Unlock-Token": "bad-token",
                    },
                )
        self.assertEqual(resp.status_code, 401)

    # ── Unlock guard tests ───────────────────────────────

    def test_resolve_dek_from_passphrase_rejects_bad_passphrase(self) -> None:
        from backend.app.services.unlock_guard import resolve_dek_from_passphrase
        row = make_account_row(passphrase="real-passphrase")
        fake_db = _FakeDb(account_key=row)

        with self.assertRaises(HTTPException) as ctx:
            resolve_dek_from_passphrase(fake_db, "admin", "wrong-passphrase")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail, "Invalid passphrase")

    def test_resolve_dek_from_passphrase_increments_failure_counter(self) -> None:
        from backend.app.services.unlock_guard import resolve_dek_from_passphrase
        row = make_account_row(passphrase="real-passphrase")
        fake_db = _FakeDb(account_key=row)

        for _ in range(3):
            try:
                resolve_dek_from_passphrase(fake_db, "admin", "wrong-passphrase")
            except HTTPException:
                pass

        self.assertEqual(row.failed_unlock_attempts, 3)

    def test_resolve_dek_from_passphrase_enforces_lockout(self) -> None:
        from backend.app.services.unlock_guard import resolve_dek_from_passphrase
        row = make_account_row(passphrase="real-passphrase")
        fake_db = _FakeDb(account_key=row)

        for _ in range(5):
            try:
                resolve_dek_from_passphrase(fake_db, "admin", "wrong-passphrase")
            except HTTPException:
                pass

        self.assertIsNotNone(row.locked_until)
        self.assertGreater(row.consecutive_lockouts, 0)

        with self.assertRaises(HTTPException) as ctx:
            resolve_dek_from_passphrase(fake_db, "admin", "wrong-passphrase")
        self.assertEqual(ctx.exception.status_code, 429)

    def test_resolve_dek_from_passphrase_returns_dek_on_success(self) -> None:
        from backend.app.services.unlock_guard import resolve_dek_from_passphrase
        row = make_account_row(passphrase="real-passphrase")
        fake_db = _FakeDb(account_key=row)

        dek = resolve_dek_from_passphrase(fake_db, "admin", "real-passphrase")

        self.assertIsInstance(dek, bytes)
        self.assertEqual(len(dek), 32)
        self.assertEqual(row.failed_unlock_attempts, 0)
        self.assertIsNone(row.locked_until)
        self.assertEqual(row.consecutive_lockouts, 0)

    def test_resolve_dek_from_passphrase_resets_lockout_on_success(self) -> None:
        from backend.app.services.unlock_guard import resolve_dek_from_passphrase
        row = make_account_row(passphrase="real-passphrase")
        row.failed_unlock_attempts = 3
        row.consecutive_lockouts = 1
        fake_db = _FakeDb(account_key=row)

        dek = resolve_dek_from_passphrase(fake_db, "admin", "real-passphrase")

        self.assertEqual(row.failed_unlock_attempts, 0)
        self.assertIsNone(row.locked_until)
        self.assertEqual(row.consecutive_lockouts, 0)


if __name__ == "__main__":
    unittest.main()
