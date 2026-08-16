from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.app.schemas import ChatMessage, ChatSettings, SettingsResponse
from backend.app.services import chat_engine
from backend.app.services import settings_service


class ChatEnginePromptTests(unittest.TestCase):
    def test_load_system_prompt_prefers_account_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            account_prompt = base / "engine" / "accounts" / "admin" / "config" / "chat_system_prompt.md"
            global_prompt = base / "backend" / "prompts" / "chat_system_prompt.md"
            account_prompt.parent.mkdir(parents=True, exist_ok=True)
            global_prompt.parent.mkdir(parents=True, exist_ok=True)
            account_prompt.write_text("ACCOUNT PROMPT", encoding="utf-8")
            global_prompt.write_text("GLOBAL PROMPT", encoding="utf-8")

            with mock.patch.object(settings_service, "BASE_DIR", base):
                with mock.patch.object(settings_service, "GLOBAL_SYSTEM_PROMPT_PATH", global_prompt):
                    with mock.patch.object(settings_service, "_is_db_storage_mode", return_value=False):
                        prompt = settings_service.get_effective_system_prompt("admin")

        self.assertEqual(prompt, "ACCOUNT PROMPT")

    def test_load_system_prompt_falls_back_to_global_then_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            global_prompt = base / "backend" / "prompts" / "chat_system_prompt.md"
            global_prompt.parent.mkdir(parents=True, exist_ok=True)
            global_prompt.write_text("GLOBAL PROMPT", encoding="utf-8")

            with mock.patch.object(settings_service, "BASE_DIR", base):
                with mock.patch.object(settings_service, "GLOBAL_SYSTEM_PROMPT_PATH", global_prompt):
                    with mock.patch.object(settings_service, "_is_db_storage_mode", return_value=False):
                        prompt = settings_service.get_effective_system_prompt("admin")
                        self.assertEqual(prompt, "GLOBAL PROMPT")

            with mock.patch.object(settings_service, "BASE_DIR", base):
                with mock.patch.object(settings_service, "GLOBAL_SYSTEM_PROMPT_PATH", base / "missing.md"):
                    with mock.patch.object(settings_service, "_is_db_storage_mode", return_value=False):
                        prompt = settings_service.get_effective_system_prompt("admin")
                        self.assertEqual(prompt, settings_service.DEFAULT_SYSTEM_PROMPT)


class ChatEngineToolCallingTests(unittest.TestCase):
    @staticmethod
    def _response_with_message(message) -> SimpleNamespace:
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def test_generate_reply_executes_get_system_prompt_tool(self) -> None:
        router = mock.Mock()
        router.model = "test-model"
        router.tutor_context_builder.build_messages.return_value = [{"role": "user", "content": "show my prompt"}]
        first_message = SimpleNamespace(
            content="",
            tool_calls=[
                SimpleNamespace(
                    id="call_1",
                    function=SimpleNamespace(name="get_system_prompt", arguments="{}"),
                )
            ],
        )
        second_message = SimpleNamespace(content="Your current system prompt is: Be concise.", tool_calls=[])
        router.client.chat.completions.create.side_effect = [
            self._response_with_message(first_message),
            self._response_with_message(second_message),
        ]

        with mock.patch.object(chat_engine, "get_router", return_value=router):
            with mock.patch.object(chat_engine, "get_effective_system_prompt", return_value="BASE PROMPT"):
                with mock.patch.object(
                    chat_engine,
                    "get_settings",
                    return_value=SettingsResponse(chat=ChatSettings(system_prompt="Be concise.")),
                ):
                    reply = chat_engine.generate_reply(
                        user_message=ChatMessage(role="user", text="show my prompt"),
                        lesson=None,
                        doc_texts=[],
                        account_id="admin",
                    )

        self.assertEqual(reply, "Your current system prompt is: Be concise.")
        self.assertEqual(router.client.chat.completions.create.call_count, 2)
        second_call = router.client.chat.completions.create.call_args_list[1]
        second_messages = second_call.kwargs["messages"]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertIn("Be concise.", second_messages[-1]["content"])

    def test_generate_reply_executes_update_system_prompt_tool(self) -> None:
        router = mock.Mock()
        router.model = "test-model"
        router.tutor_context_builder.build_messages.return_value = [{"role": "user", "content": "make the tutor stricter"}]
        first_message = SimpleNamespace(
            content="",
            tool_calls=[
                SimpleNamespace(
                    id="call_2",
                    function=SimpleNamespace(
                        name="update_system_prompt",
                        arguments='{"system_prompt":"Be strict and cite context."}',
                    ),
                )
            ],
        )
        second_message = SimpleNamespace(content="Updated the tutor settings.", tool_calls=[])
        router.client.chat.completions.create.side_effect = [
            self._response_with_message(first_message),
            self._response_with_message(second_message),
        ]

        with mock.patch.object(chat_engine, "get_router", return_value=router):
            with mock.patch.object(chat_engine, "get_effective_system_prompt", return_value="BASE PROMPT"):
                with mock.patch.object(
                    chat_engine,
                    "update_settings",
                    return_value=SettingsResponse(chat=ChatSettings(system_prompt="Be strict and cite context.")),
                ) as mock_update:
                    reply = chat_engine.generate_reply(
                        user_message=ChatMessage(role="user", text="make the tutor stricter"),
                        lesson=None,
                        doc_texts=[],
                        account_id="admin",
                    )

        self.assertEqual(reply, "Updated the tutor settings.")
        self.assertEqual(router.client.chat.completions.create.call_count, 2)
        mock_update.assert_called_once()
        self.assertEqual(
            mock_update.call_args.kwargs["patch"].chat.system_prompt,
            "Be strict and cite context.",
        )


if __name__ == "__main__":
    unittest.main()
