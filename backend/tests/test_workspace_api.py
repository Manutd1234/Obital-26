from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.api.common import require_account
from backend.app.services import workspace_service


def _mock_require_account():
    return ("admin", None)


class WorkspaceApiLocalTests(unittest.TestCase):
    """GET/PUT contract in local-file mode (no DB), mirroring test_settings_api."""

    def setUp(self) -> None:
        app.dependency_overrides[require_account] = _mock_require_account
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(require_account, None)

    def test_get_returns_default_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(workspace_service, "BASE_DIR", Path(td)),
                mock.patch.object(workspace_service, "_is_db_storage_mode", return_value=False),
            ):
                resp = self.client.get("/workspace", params={"account_id": "admin"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], {"js": "", "python": ""})
        self.assertEqual(body["tasks"], {})
        self.assertEqual(body["sessions"], [])

    def test_put_then_get_roundtrip(self) -> None:
        doc = {
            "code": {"js": "console.log(1)", "python": "print(1)"},
            "tasks": {"2026-06-27": [{"id": "t1", "text": "revise", "done": False}]},
            "sessions": [
                {"id": "s1", "date": "2026-06-27", "duration_seconds": 1500, "completed_at": "2026-06-27T10:00:00Z"}
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(workspace_service, "BASE_DIR", Path(td)),
                mock.patch.object(workspace_service, "_is_db_storage_mode", return_value=False),
            ):
                put = self.client.put("/workspace", params={"account_id": "admin"}, json=doc)
                get = self.client.get("/workspace", params={"account_id": "admin"})

        self.assertEqual(put.status_code, 200)
        self.assertEqual(get.status_code, 200)
        body = get.json()
        self.assertEqual(body["code"]["python"], "print(1)")
        self.assertEqual(body["tasks"]["2026-06-27"][0]["text"], "revise")
        self.assertEqual(body["sessions"][0]["duration_seconds"], 1500)


# ── Fake artifact DB to exercise the E2EE path without a real database ──


class _FakeArtifactQuery:
    def __init__(self, store: dict) -> None:
        self.store = store

    def where(self, *args, **kwargs) -> "_FakeArtifactQuery":
        return self

    def first(self):
        return self.store.get("row")


class _FakeArtifactDb:
    def __init__(self) -> None:
        self.store: dict = {}

    def query(self, _model) -> _FakeArtifactQuery:
        return _FakeArtifactQuery(self.store)

    def add(self, row) -> None:
        self.store["row"] = row

    def commit(self) -> None:
        return None


class _FakeSessionFactory:
    def __init__(self, db: _FakeArtifactDb) -> None:
        self.db = db

    @contextmanager
    def __call__(self):
        yield self.db


class WorkspaceEncryptionTests(unittest.TestCase):
    """The workspace blob must be encrypted at rest in non-sqlite DB mode."""

    def test_workspace_encrypted_at_rest_and_decrypts(self) -> None:
        from backend.app.schemas import WorkspaceData

        fake_db = _FakeArtifactDb()
        dek = b"k" * 32
        secret = "secret-task-marker-xyz"
        data = WorkspaceData.model_validate(
            {"tasks": {"2026-06-27": [{"id": "t1", "text": secret, "done": False}]}}
        )

        with (
            mock.patch.object(workspace_service, "_is_db_storage_mode", return_value=True),
            mock.patch("backend.app.services.db.SessionLocal", new=_FakeSessionFactory(fake_db)),
            mock.patch("backend.app.services.db.is_sqlite_mode", return_value=False),
        ):
            workspace_service.save_workspace(account_id="admin", data=data, user_dek=dek)
            stored = fake_db.store["row"].content
            # ciphertext must not contain the plaintext task marker
            self.assertNotIn(secret.encode("utf-8"), bytes(stored))

            loaded = workspace_service.get_workspace(account_id="admin", user_dek=dek)

        self.assertEqual(loaded.tasks["2026-06-27"][0].text, secret)


if __name__ == "__main__":
    unittest.main()
