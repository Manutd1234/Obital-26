from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from frontend.app import ObitalTextualApp


class RemovedCommandsTests(unittest.TestCase):
    def test_settings_intent_method_removed(self) -> None:
        app = ObitalTextualApp()
        self.assertFalse(hasattr(app, "_is_settings_intent"))

    def test_search_command_falls_through_to_chat(self) -> None:
        app = ObitalTextualApp()
        app.chat_pane.append_line = mock.Mock()
        app._focus_chat_input = mock.Mock()
        app.bridge = mock.Mock()
        app.bridge.is_alive = False

        event = mock.Mock()
        event.text = "/search trees"

        asyncio.run(app.on_send_chat_message(event))

        app.chat_pane.append_line.assert_any_call("You: /search trees")

    def test_settings_command_falls_through_to_chat(self) -> None:
        app = ObitalTextualApp()
        app.chat_pane.append_line = mock.Mock()
        app._focus_chat_input = mock.Mock()
        app.bridge = mock.Mock()
        app.bridge.is_alive = False

        event = mock.Mock()
        event.text = "/settings"

        asyncio.run(app.on_send_chat_message(event))

        app.chat_pane.append_line.assert_any_call("You: /settings")

    def test_dashboard_command_falls_through_to_chat(self) -> None:
        app = ObitalTextualApp()
        app.chat_pane.append_line = mock.Mock()
        app._focus_chat_input = mock.Mock()
        app.bridge = mock.Mock()
        app.bridge.is_alive = False

        event = mock.Mock()
        event.text = "/dashboard"

        asyncio.run(app.on_send_chat_message(event))

        app.chat_pane.append_line.assert_any_call("You: /dashboard")

    def test_settings_intent_text_falls_through_to_chat(self) -> None:
        app = ObitalTextualApp()
        app.chat_pane.append_line = mock.Mock()
        app._focus_chat_input = mock.Mock()
        app.bridge = mock.Mock()
        app.bridge.is_alive = False

        event = mock.Mock()
        event.text = "open settings"

        asyncio.run(app.on_send_chat_message(event))

        app.chat_pane.append_line.assert_any_call("You: open settings")

    def test_sync_command_still_works(self) -> None:
        app = ObitalTextualApp()
        app.chat_pane.append_line = mock.Mock()
        app._focus_chat_input = mock.Mock()
        app.state.account_id = "admin"
        app._start_ops_action = mock.AsyncMock()

        event = mock.Mock()
        event.text = "/sync"

        asyncio.run(app.on_send_chat_message(event))

        app._focus_chat_input.assert_called_once()


if __name__ == "__main__":
    unittest.main()
