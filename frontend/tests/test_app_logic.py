from __future__ import annotations

import asyncio
import unittest

from unittest import mock
from unittest.mock import AsyncMock

from frontend.app import _SIGNED_IN_RE
from frontend.app import ObitalTextualApp
from frontend.ui.unlock_modal import UnlockModal


class FrontendLogicTests(unittest.TestCase):
    def test_signed_in_line_parser(self) -> None:
        line = "Signed in as: admin | Account id: admin | Points: 30"
        match = _SIGNED_IN_RE.search(line)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group("user"), "admin")
        self.assertEqual(match.group("account"), "admin")

    def test_sync_actions_require_account_id(self) -> None:
        app = ObitalTextualApp()
        app.chat_pane.append_line = mock.Mock()
        app.state.account_id = ""

        asyncio.run(app._start_ops_action("sync_cloud"))

        app.chat_pane.append_line.assert_called_with("[ops] no account selected for sync")
        self.assertIsNone(app._ops_task)

    def test_sync_actions_start_without_cli_sign_in(self) -> None:
        app = ObitalTextualApp()
        app.state.account_id = "admin"
        called: list[str] = []

        async def _fake_run_sync_push() -> None:
            called.append("sync")
            return None

        app._run_sync_push = _fake_run_sync_push  # type: ignore[method-assign]

        async def _run() -> None:
            await app._start_ops_action("sync_cloud")
            self.assertIsNotNone(app._ops_task)
            if app._ops_task is not None:
                await app._ops_task

        asyncio.run(_run())
        self.assertEqual(called, ["sync"])

    def test_unlock_modal_submit_sets_unlock_token(self) -> None:
        app = ObitalTextualApp()
        app.chat_pane.append_line = mock.Mock()
        app.state.account_id = "admin"
        app.api.unlock_account = AsyncMock(return_value="token-123")

        modal = mock.Mock(spec=UnlockModal)
        event = UnlockModal.Submitted(modal, "correct-passphrase")

        asyncio.run(app.on_unlock_modal_submitted(event))

        app.api.unlock_account.assert_awaited_once_with("correct-passphrase")
        self.assertEqual(app.state.unlock_token, "token-123")
        app.chat_pane.append_line.assert_called_with("[ui] account unlocked")
        modal.dismiss.assert_called_once_with("token-123")

    def test_sync_slash_command_dispatches_ops_action(self) -> None:
        app = ObitalTextualApp()
        app.chat_pane.append_line = mock.Mock()
        app._focus_chat_input = mock.Mock()

        async def _fake_start(action: str) -> None:
            self.assertEqual(action, "sync_cloud")

        app._start_ops_action = _fake_start  # type: ignore[method-assign]
        event = mock.Mock()
        event.text = "/sync"

        asyncio.run(app.on_send_chat_message(event))

        app._focus_chat_input.assert_called_once()


if __name__ == "__main__":
    unittest.main()
