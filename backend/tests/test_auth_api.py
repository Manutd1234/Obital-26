from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.app.main import app
from backend.app.services.models import AccountKeyModel, AccountUnlockSessionModel
from backend.app.services.unlock_session import (
    MissingClientBindingError,
    create_unlock_session,
    resolve_dek,
    resolve_dek_for_account,
)
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

    def order_by(self, *args, **kwargs) -> "_FakeQuery":
        return self


class _FakeExecuteResult:
    def __init__(self, rowcount: int = 0) -> None:
        self.rowcount = rowcount


@dataclass
class _FakeDb:
    account_key: AccountKeyModel | None = None
    unlock_sessions: list[AccountUnlockSessionModel] = field(default_factory=list)
    commits: int = 0

    def query(self, model):
        if model is AccountKeyModel:
            return _FakeQuery(self.account_key)
        if model is AccountUnlockSessionModel:
            return _FakeQuery(self.unlock_sessions[0] if self.unlock_sessions else None)
        raise AssertionError(f"Unexpected model query: {model}")

    def add(self, row) -> None:
        if isinstance(row, AccountKeyModel):
            self.account_key = row
            return
        if isinstance(row, AccountUnlockSessionModel):
            self.unlock_sessions = [row]
            return
        raise AssertionError(f"Unexpected model add: {type(row)!r}")

    def delete(self, row) -> None:
        self.unlock_sessions = [item for item in self.unlock_sessions if item is not row]

    def execute(self, statement) -> _FakeExecuteResult:
        return _FakeExecuteResult(rowcount=0)

    def commit(self) -> None:
        self.commits += 1


class _FakeSessionFactory:
    def __init__(self, db: _FakeDb) -> None:
        self.db = db

    @contextmanager
    def __call__(self):
        yield self.db


class AuthApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.secret_patch = mock.patch.dict(os.environ, {"UNLOCK_TOKEN_SECRET": "test-unlock-secret"}, clear=False)
        self.secret_patch.start()
        from backend.app.services import unlock_session
        unlock_session._token_failures.clear()

    def tearDown(self) -> None:
        from backend.app.services import unlock_session
        unlock_session._token_failures.clear()
        self.secret_patch.stop()

    @staticmethod
    def _request(user_agent: str = "test-agent-01", client_id: str = "test-client-001") -> Request:
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

    def test_unlock_token_is_bound_to_client_context(self) -> None:
        request = self._request("agent-1", "client-ui-01")
        fake_db = _FakeDb()
        token = create_unlock_session(account_id="admin", dek=b"x" * 32, unlock_version=3, request=request, db=fake_db)

        resolved = resolve_dek(account_id="admin", token=token, unlock_version=3, request=request, db=fake_db)
        mismatched = resolve_dek(
            account_id="admin",
            token=token,
            unlock_version=3,
            request=self._request("agent-2", "other-client-id"),
            db=fake_db,
        )

        self.assertEqual(resolved, b"x" * 32)
        self.assertIsNone(mismatched)

    def test_create_unlock_session_rejects_missing_client_id(self) -> None:
        request = self._request("agent-1", "")
        fake_db = _FakeDb()

        with self.assertRaises(MissingClientBindingError):
            create_unlock_session(account_id="admin", dek=b"x" * 32, unlock_version=3, request=request, db=fake_db)

        self.assertEqual(fake_db.unlock_sessions, [])

    def test_resolve_dek_rejects_legacy_empty_client_binding(self) -> None:
        request = self._request("agent-1", "client-ui-01")
        fake_db = _FakeDb(account_key=make_account_row())
        token = create_unlock_session(account_id="admin", dek=b"x" * 32, unlock_version=3, request=request, db=fake_db)
        fake_db.unlock_sessions[0].client_id = ""

        self.assertIsNone(resolve_dek(account_id="admin", token=token, unlock_version=3, request=request, db=fake_db))

    def test_resolve_dek_rejects_legacy_empty_user_agent_binding(self) -> None:
        request = self._request("agent-1", "client-ui-01")
        fake_db = _FakeDb(account_key=make_account_row())
        token = create_unlock_session(account_id="admin", dek=b"x" * 32, unlock_version=3, request=request, db=fake_db)
        fake_db.unlock_sessions[0].user_agent = ""

        self.assertIsNone(resolve_dek(account_id="admin", token=token, unlock_version=3, request=request, db=fake_db))

    def test_resolve_dek_for_account_rejects_legacy_empty_client_binding(self) -> None:
        request = self._request("agent-1", "client-ui-01")
        fake_db = _FakeDb(account_key=make_account_row())
        create_unlock_session(account_id="admin", dek=b"x" * 32, unlock_version=3, request=request, db=fake_db)
        fake_db.unlock_sessions[0].client_id = ""

        self.assertIsNone(resolve_dek_for_account(account_id="admin", db=fake_db))

    def test_unlock_token_failures_are_rate_limited(self) -> None:
        row = make_account_row()
        fake_db = _FakeDb(account_key=row)
        request = self._request("agent-1", "client-ui-01")

        with mock.patch("backend.app.services.unlock_session._TOKEN_MAX_FAILED_ATTEMPTS", 2):
            self.assertIsNone(
                resolve_dek(account_id="admin", token="bad-token", unlock_version=1, request=request, db=fake_db)
            )
            self.assertIsNone(
                resolve_dek(account_id="admin", token="bad-token", unlock_version=1, request=request, db=fake_db)
            )

            with self.assertRaises(HTTPException) as ctx:
                resolve_dek(account_id="admin", token="bad-token", unlock_version=1, request=request, db=fake_db)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIsNone(row.locked_until)

    def test_unlock_invalidates_when_version_changes(self) -> None:
        request = self._request("agent-1", "client-ui-01")
        fake_db = _FakeDb()
        token = create_unlock_session(account_id="admin", dek=b"y" * 32, unlock_version=1, request=request, db=fake_db)

        self.assertEqual(
            resolve_dek(account_id="admin", token=token, unlock_version=1, request=request, db=fake_db),
            b"y" * 32,
        )
        self.assertIsNone(resolve_dek(account_id="admin", token=token, unlock_version=2, request=request, db=fake_db))

    def test_unlock_rate_limits_after_repeated_failures(self) -> None:
        row = make_account_row()
        fake_db = _FakeDb(account_key=row)

        with mock.patch("backend.app.api.auth.db_enabled", return_value=True):
            with mock.patch("backend.app.api.auth.SessionLocal", new=_FakeSessionFactory(fake_db)):
                for _ in range(5):
                    resp = self.client.post(
                        "/auth/unlock",
                        params={"account_id": "admin"},
                        json={"passphrase": "wrong-passphrase"},
                        headers={"User-Agent": "agent-1", "X-Client-Id": "client-ui-01"},
                    )
                    self.assertEqual(resp.status_code, 401)

                blocked = self.client.post(
                    "/auth/unlock",
                    params={"account_id": "admin"},
                    json={"passphrase": "wrong-passphrase"},
                    headers={"User-Agent": "agent-1", "X-Client-Id": "client-ui-01"},
                )

        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["detail"], "Unlock temporarily locked")

    def test_unlock_fails_for_unknown_account_without_key_material(self) -> None:
        fake_db = _FakeDb(account_key=None)

        with mock.patch("backend.app.api.auth.db_enabled", return_value=True):
            with mock.patch("backend.app.api.auth.SessionLocal", new=_FakeSessionFactory(fake_db)):
                with mock.patch("backend.app.api.auth.account_exists", return_value=False):
                    resp = self.client.post(
                        "/auth/unlock",
                        params={"account_id": "ghost_user"},
                        json={"passphrase": "whatever"},
                        headers={"User-Agent": "agent-1", "X-Client-Id": "client-ui-01"},
                    )

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"], "Account not found")

    def test_unlock_replaces_stale_local_key_with_cloud_key(self) -> None:
        from backend.app.api.auth import _perform_unlock

        local_row = make_account_row(passphrase="old-local-passphrase")
        cloud_row = make_account_row(passphrase="current-cloud-passphrase")
        cloud_row.unlock_version = 51
        fake_db = _FakeDb(account_key=local_row)

        with mock.patch("backend.app.api.auth._cloud_key_row", return_value=cloud_row):
            token, dek = _perform_unlock(
                db=fake_db,
                account_id="admin",
                passphrase="current-cloud-passphrase",
                request=self._request(),
            )

        self.assertTrue(token)
        self.assertIsInstance(dek, bytes)
        self.assertEqual(local_row.salt_b64, cloud_row.salt_b64)
        self.assertEqual(local_row.argon2_params_json, cloud_row.argon2_params_json)
        self.assertEqual(local_row.wrapped_dek_b64, cloud_row.wrapped_dek_b64)
        self.assertEqual(local_row.unlock_version, 51)

    def test_unlock_rejects_missing_client_id(self) -> None:
        resp = self.client.post(
            "/auth/unlock",
            params={"account_id": "admin"},
            json={"passphrase": "whatever"},
            headers={"User-Agent": "agent-1"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("X-Client-Id", resp.json()["detail"])

    def test_unlock_rejects_short_client_id(self) -> None:
        resp = self.client.post(
            "/auth/unlock",
            params={"account_id": "admin"},
            json={"passphrase": "whatever"},
            headers={"User-Agent": "agent-1", "X-Client-Id": "short"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("X-Client-Id", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
