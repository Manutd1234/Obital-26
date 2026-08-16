from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.api.common import require_account
from backend.app.services import settings_service


def _mock_require_account():
    return ("admin", None)


class SettingsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[require_account] = _mock_require_account
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(require_account, None)

    def test_get_settings_returns_effective_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            global_prompt = base / "backend" / "prompts" / "chat_system_prompt.md"
            global_prompt.parent.mkdir(parents=True, exist_ok=True)
            global_prompt.write_text("GLOBAL PROMPT", encoding="utf-8")

            with (
                mock.patch.object(settings_service, "BASE_DIR", base),
                mock.patch.object(settings_service, "GLOBAL_SYSTEM_PROMPT_PATH", global_prompt),
                mock.patch.object(settings_service, "_is_db_storage_mode", return_value=False),
            ):
                resp = self.client.get("/settings", params={"account_id": "admin"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["chat"]["system_prompt"], "GLOBAL PROMPT")

    def test_patch_settings_updates_account_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with (
                mock.patch.object(settings_service, "BASE_DIR", base),
                mock.patch.object(settings_service, "_is_db_storage_mode", return_value=False),
            ):
                resp = self.client.patch(
                    "/settings",
                    params={"account_id": "admin"},
                    json={"chat": {"system_prompt": "Updated prompt"}},
                )

                prompt_path = (
                    base / "engine" / "accounts" / "admin" / "config" / "chat_system_prompt.md"
                )
                prompt_exists = prompt_path.exists()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["chat"]["system_prompt"], "Updated prompt")
        self.assertTrue(prompt_exists)

    def test_patch_settings_rejects_empty_prompt(self) -> None:
        resp = self.client.patch(
            "/settings",
            params={"account_id": "admin"},
            json={"chat": {"system_prompt": "   "}},
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
