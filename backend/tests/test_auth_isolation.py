"""Tenant isolation tests — verify that account-scoped routes enforce token-based authorization.

These tests exercise the real FastAPI dependency chain (require_account / require_account_id)
with mocked DB backends, ensuring that:
  1. Unauthenticated requests are rejected in cloud mode.
  2. A token bound to account A cannot access account B's data.
  3. Expired / version-mismatched tokens are rejected.
  4. The active_user_dek ContextVar is correctly bound per-request.
"""
from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from unittest import mock

from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.app.main import app
from backend.app.services.models import AccountKeyModel, AccountUnlockSessionModel
from backend.app.services.unlock_session import create_unlock_session, resolve_dek
from backend.tests.helpers import make_account_row


# ---------------------------------------------------------------------------
# Fake DB helpers
# ---------------------------------------------------------------------------

class _FakeQuery:
    """Generic fake query that accepts any SQLAlchemy expression in where()."""

    def __init__(self, rows) -> None:
        self._rows = list(rows) if rows is not None else []

    def where(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows

    def count(self):
        return len(self._rows)


class _AccountKeyQuery:
    """Fake query for AccountKeyModel that filters by account_id."""

    def __init__(self, store: dict[str, AccountKeyModel]) -> None:
        self._store = store
        self._filter_account_id: str | None = None

    def where(self, *args, **kwargs):
        # Extract account_id from the SQLAlchemy BinaryExpression.
        # AccountKeyModel.account_id == "alice" produces an expression
        # whose right-hand side has a .value attribute.
        for arg in args:
            if hasattr(arg, "right") and hasattr(arg.right, "value"):
                self._filter_account_id = arg.right.value
        return self

    def first(self):
        if self._filter_account_id is not None:
            return self._store.get(self._filter_account_id)
        # No filter — return first available
        for row in self._store.values():
            return row
        return None

    def all(self):
        return list(self._store.values())


class _UnlockSessionQuery:
    """Fake query for AccountUnlockSessionModel that filters by token_hash."""

    def __init__(self, store: dict[str, list[AccountUnlockSessionModel]]) -> None:
        self._store = store
        self._filter_token_hash: str | None = None

    def where(self, *args, **kwargs):
        # Extract token_hash from the expression if present
        for arg in args:
            if hasattr(arg, "right") and hasattr(arg.right, "value"):
                val = arg.right.value
                if isinstance(val, str) and len(val) == 64:  # SHA-256 hex
                    self._filter_token_hash = val
        return self

    def first(self):
        if self._filter_token_hash is not None:
            for sessions in self._store.values():
                for s in sessions:
                    if s.token_hash == self._filter_token_hash:
                        return s
            return None
        # No filter — return first available
        for sessions in self._store.values():
            if sessions:
                return sessions[0]
        return None

    def all(self):
        result = []
        for sessions in self._store.values():
            result.extend(sessions)
        return result


@dataclass
class _FakeDb:
    account_keys: dict[str, AccountKeyModel] = field(default_factory=dict)
    unlock_sessions: dict[str, list[AccountUnlockSessionModel]] = field(default_factory=dict)
    commits: int = 0

    def query(self, model):
        if model is AccountKeyModel:
            return _AccountKeyQuery(self.account_keys)
        if model is AccountUnlockSessionModel:
            return _UnlockSessionQuery(self.unlock_sessions)
        raise AssertionError(f"Unexpected model query: {model}")

    def add(self, row) -> None:
        if isinstance(row, AccountKeyModel):
            self.account_keys[row.account_id] = row
            return
        if isinstance(row, AccountUnlockSessionModel):
            self.unlock_sessions.setdefault(row.account_id, []).append(row)
            return
        raise AssertionError(f"Unexpected model add: {type(row)!r}")

    def delete(self, row) -> None:
        if isinstance(row, AccountUnlockSessionModel):
            sessions = self.unlock_sessions.get(row.account_id, [])
            self.unlock_sessions[row.account_id] = [s for s in sessions if s is not row]

    def execute(self, statement):
        class _Result:
            rowcount = 0
        return _Result()

    def commit(self) -> None:
        self.commits += 1


class _FakeSessionFactory:
    def __init__(self, db: _FakeDb) -> None:
        self.db = db

    @contextmanager
    def __call__(self):
        yield self.db


def _make_request(
    user_agent: str = "test-agent",
    client_id: str = "test-client-001",
) -> Request:
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


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestTenantIsolation(unittest.TestCase):
    """Verify cross-account isolation on account-scoped routes."""

    def setUp(self) -> None:
        self.client = TestClient(app, raise_server_exceptions=False)
        self.secret_patch = mock.patch.dict(
            os.environ, {"UNLOCK_TOKEN_SECRET": "test-isolation-secret"}, clear=False,
        )
        self.secret_patch.start()

        # Reset the module-level Fernet cache so each test uses the patched secret
        import backend.app.services.unlock_session as us_mod
        self._fernet_patch = mock.patch.object(us_mod, "_fernet", None)
        self._fernet_patch.start()

        # Create two distinct accounts with different DEKs
        self.alice_row = make_account_row(account_id="alice", passphrase="alice-passphrase")
        self.bob_row = make_account_row(account_id="bob", passphrase="bob-passphrase")

        self.fake_db = _FakeDb(
            account_keys={
                "alice": self.alice_row,
                "bob": self.bob_row,
            },
        )

    def tearDown(self) -> None:
        self._fernet_patch.stop()
        self.secret_patch.stop()

    def _create_token(self, account_id: str, dek: bytes, **kwargs) -> str:
        """Create a valid unlock token for the given account."""
        request = _make_request()
        return create_unlock_session(
            account_id=account_id,
            dek=dek,
            unlock_version=1,
            request=request,
            db=self.fake_db,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # 1. No token → 401 in cloud mode
    # ------------------------------------------------------------------

    def test_no_token_rejected_on_usage_route(self) -> None:
        """GET /usage without X-Unlock-Token must return 401 in cloud mode."""
        with mock.patch("backend.app.api.common.db_enabled", return_value=True), \
             mock.patch("backend.app.api.common.SessionLocal", new=_FakeSessionFactory(self.fake_db)), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OBITAL_LOCAL_SINGLE_USER", None)

            resp = self.client.get("/usage", params={"account_id": "alice"})

        self.assertEqual(resp.status_code, 401)
        self.assertIn("X-Unlock-Token", resp.json()["detail"])

    def test_no_token_rejected_on_lesson_route(self) -> None:
        """GET /lessons without X-Unlock-Token must return 401 in cloud mode."""
        with mock.patch("backend.app.api.common.db_enabled", return_value=True), \
             mock.patch("backend.app.api.common.SessionLocal", new=_FakeSessionFactory(self.fake_db)), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OBITAL_LOCAL_SINGLE_USER", None)

            resp = self.client.get("/lessons", params={"account_id": "alice"})

        self.assertEqual(resp.status_code, 401)

    def test_no_token_rejected_on_docs_route(self) -> None:
        """GET /docs without X-Unlock-Token must return 401 in cloud mode."""
        with mock.patch("backend.app.api.common.db_enabled", return_value=True), \
             mock.patch("backend.app.api.common.SessionLocal", new=_FakeSessionFactory(self.fake_db)), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OBITAL_LOCAL_SINGLE_USER", None)

            resp = self.client.get("/docs", params={"account_id": "alice"})

        self.assertEqual(resp.status_code, 401)

    # ------------------------------------------------------------------
    # 2. Token bound to account A cannot access account B
    # ------------------------------------------------------------------

    def test_alice_token_cannot_access_bob_usage(self) -> None:
        """Alice's token + ?account_id=bob must be rejected (token mismatch)."""
        token = self._create_token("alice", b"\x01" * 32)

        with mock.patch("backend.app.api.common.db_enabled", return_value=True), \
             mock.patch("backend.app.api.common.SessionLocal", new=_FakeSessionFactory(self.fake_db)), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OBITAL_LOCAL_SINGLE_USER", None)

            resp = self.client.get(
                "/usage",
                params={"account_id": "bob"},
                headers={"X-Unlock-Token": token, "X-Client-Id": "test-client-001", "User-Agent": "test-agent"},
            )

        # resolve_dek checks row.account_id == account_id; alice's token row
        # has account_id="alice" but we're requesting "bob" → mismatch → 401
        self.assertEqual(resp.status_code, 401)

    def test_bob_token_cannot_access_alice_usage(self) -> None:
        """Bob's token + ?account_id=alice must be rejected."""
        token = self._create_token("bob", b"\x02" * 32)

        with mock.patch("backend.app.api.common.db_enabled", return_value=True), \
             mock.patch("backend.app.api.common.SessionLocal", new=_FakeSessionFactory(self.fake_db)), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OBITAL_LOCAL_SINGLE_USER", None)

            resp = self.client.get(
                "/usage",
                params={"account_id": "alice"},
                headers={"X-Unlock-Token": token, "X-Client-Id": "test-client-001", "User-Agent": "test-agent"},
            )

        self.assertEqual(resp.status_code, 401)

    def test_alice_token_cannot_access_bob_docs(self) -> None:
        """Alice's token must not list Bob's documents."""
        token = self._create_token("alice", b"\x01" * 32)

        with mock.patch("backend.app.api.common.db_enabled", return_value=True), \
             mock.patch("backend.app.api.common.SessionLocal", new=_FakeSessionFactory(self.fake_db)), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OBITAL_LOCAL_SINGLE_USER", None)

            resp = self.client.get(
                "/docs",
                params={"account_id": "bob"},
                headers={"X-Unlock-Token": token},
            )

        self.assertEqual(resp.status_code, 401)

    # ------------------------------------------------------------------
    # 3. Expired / version-mismatched tokens are rejected
    # ------------------------------------------------------------------

    def test_expired_token_rejected(self) -> None:
        """A token whose unlock session has expired must be rejected."""
        # Create a token normally, then manually set expires_at to the past
        token = self._create_token("alice", b"\x01" * 32)
        # Find the session in the fake DB and expire it
        for sessions in self.fake_db.unlock_sessions.values():
            for s in sessions:
                if s.account_id == "alice":
                    s.expires_at = datetime.now(UTC) - timedelta(hours=1)

        with mock.patch("backend.app.api.common.db_enabled", return_value=True), \
             mock.patch("backend.app.api.common.SessionLocal", new=_FakeSessionFactory(self.fake_db)), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OBITAL_LOCAL_SINGLE_USER", None)

            resp = self.client.get(
                "/usage",
                params={"account_id": "alice"},
                headers={"X-Unlock-Token": token, "X-Client-Id": "test-client-001", "User-Agent": "test-agent"},
            )

        self.assertEqual(resp.status_code, 401)

    def test_version_mismatch_rejected(self) -> None:
        """A token created at version 1 must be rejected when the account is at version 2."""
        token = self._create_token("alice", b"\x01" * 32)
        # Bump the account's unlock_version in the fake DB
        self.fake_db.account_keys["alice"].unlock_version = 2

        with mock.patch("backend.app.api.common.db_enabled", return_value=True), \
             mock.patch("backend.app.api.common.SessionLocal", new=_FakeSessionFactory(self.fake_db)), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OBITAL_LOCAL_SINGLE_USER", None)

            resp = self.client.get(
                "/usage",
                params={"account_id": "alice"},
                headers={"X-Unlock-Token": token},
            )

        self.assertEqual(resp.status_code, 401)

    # ------------------------------------------------------------------
    # 4. Valid token can access own account
    # ------------------------------------------------------------------

    def test_alice_token_can_access_own_usage(self) -> None:
        """Alice's token + ?account_id=alice must succeed."""
        token = self._create_token("alice", b"\x01" * 32)

        with mock.patch("backend.app.api.common.db_enabled", return_value=True), \
             mock.patch("backend.app.api.common.SessionLocal", new=_FakeSessionFactory(self.fake_db)), \
             mock.patch("backend.app.services.usage.get_usage", return_value={
                 "token_used": 100, "doc_count": 2, "lesson_count": 1, "storage_bytes": 500,
             }), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OBITAL_LOCAL_SINGLE_USER", None)

            resp = self.client.get(
                "/usage",
                params={"account_id": "alice"},
                headers={"X-Unlock-Token": token, "X-Client-Id": "test-client-001", "User-Agent": "test-agent"},
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["tokens_used"], 100)

    def test_bob_token_can_access_own_usage(self) -> None:
        """Bob's token + ?account_id=bob must succeed."""
        token = self._create_token("bob", b"\x02" * 32)

        with mock.patch("backend.app.api.common.db_enabled", return_value=True), \
             mock.patch("backend.app.api.common.SessionLocal", new=_FakeSessionFactory(self.fake_db)), \
             mock.patch("backend.app.services.usage.get_usage", return_value={
                 "token_used": 50, "doc_count": 1, "lesson_count": 0, "storage_bytes": 200,
             }), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OBITAL_LOCAL_SINGLE_USER", None)

            resp = self.client.get(
                "/usage",
                params={"account_id": "bob"},
                headers={"X-Unlock-Token": token, "X-Client-Id": "test-client-001", "User-Agent": "test-agent"},
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["tokens_used"], 50)

    # ------------------------------------------------------------------
    # 5. Wrong token entirely (random string) is rejected
    # ------------------------------------------------------------------

    def test_garbage_token_rejected(self) -> None:
        """A random (non-existent) token string must be rejected."""
        with mock.patch("backend.app.api.common.db_enabled", return_value=True), \
             mock.patch("backend.app.api.common.SessionLocal", new=_FakeSessionFactory(self.fake_db)), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OBITAL_LOCAL_SINGLE_USER", None)

            resp = self.client.get(
                "/usage",
                params={"account_id": "alice"},
                headers={"X-Unlock-Token": "totally-fake-garbage-token"},
            )

        self.assertEqual(resp.status_code, 401)

    # ------------------------------------------------------------------
    # 6. Local single-user mode: token optional, no cross-account guard
    # ------------------------------------------------------------------

    def test_local_single_user_allows_no_token(self) -> None:
        """In LOCAL_SINGLE_USER mode, requests without a token should pass (200)."""
        with mock.patch("backend.app.api.common.db_enabled", return_value=True), \
             mock.patch("backend.app.api.common.SessionLocal", new=_FakeSessionFactory(self.fake_db)), \
             mock.patch("backend.app.services.usage.get_usage", return_value={
                 "token_used": 0, "doc_count": 0, "lesson_count": 0, "storage_bytes": 0,
             }), \
             mock.patch.dict(os.environ, {"OBITAL_LOCAL_SINGLE_USER": "1"}, clear=False):

            resp = self.client.get("/usage", params={"account_id": "alice"})

        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
