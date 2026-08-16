from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from backend.app.services import llm_json


class FakeAsyncOpenAI:
    closed = False

    def __init__(self, *args, **kwargs) -> None:
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=mock.AsyncMock(
                    return_value=SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
                    )
                )
            )
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        type(self).closed = True


class FakeAsyncStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeStreamingAsyncOpenAI:
    closed = False
    create_kwargs = None

    def __init__(self, *args, **kwargs) -> None:
        async def create(**create_kwargs):
            type(self).create_kwargs = create_kwargs
            chunks = [
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content="checking ", content=""))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content="json", content='{"ok"'))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content=None, content=': true}'))]),
            ]
            return FakeAsyncStream(chunks)

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        type(self).closed = True


class LlmJsonTests(unittest.TestCase):
    def test_call_json_model_closes_async_client_before_returning(self) -> None:
        FakeAsyncOpenAI.closed = False
        settings = SimpleNamespace(
            deepseek_api_key="test-key",
            deepseek_base_url="https://example.test",
            deepseek_model="test-model",
        )

        with mock.patch.object(llm_json, "load_settings", return_value=settings):
            with mock.patch("openai.AsyncOpenAI", FakeAsyncOpenAI):
                result = asyncio.run(llm_json.call_json_model(
                    role="tester",
                    payload={"task": "test"},
                    fallback={"ok": False},
                ))

        self.assertEqual(result, {"ok": True})
        self.assertTrue(FakeAsyncOpenAI.closed)

    def test_call_json_model_streams_reasoning_when_enabled(self) -> None:
        FakeStreamingAsyncOpenAI.closed = False
        FakeStreamingAsyncOpenAI.create_kwargs = None
        settings = SimpleNamespace(
            deepseek_api_key="test-key",
            deepseek_base_url="https://example.test",
            deepseek_model="test-model",
            deepseek_thinking_enabled=True,
            deepseek_reasoning_effort="high",
        )
        events = []

        with mock.patch.object(llm_json, "load_settings", return_value=settings):
            with mock.patch("openai.AsyncOpenAI", FakeStreamingAsyncOpenAI):
                result = asyncio.run(llm_json.call_json_model(
                    role="tester",
                    payload={"task": "test"},
                    fallback={"ok": False},
                    event_callback=events.append,
                ))

        self.assertEqual(result, {"ok": True})
        self.assertTrue(FakeStreamingAsyncOpenAI.closed)
        self.assertEqual(FakeStreamingAsyncOpenAI.create_kwargs["stream"], True)
        self.assertEqual(FakeStreamingAsyncOpenAI.create_kwargs["reasoning_effort"], "high")
        self.assertEqual(FakeStreamingAsyncOpenAI.create_kwargs["extra_body"], {"thinking": {"type": "enabled"}})
        self.assertEqual([event["type"] for event in events], ["model_start", "reasoning_delta", "reasoning_delta", "model_done"])

    def test_call_json_model_with_tools_returns_json_when_no_tool_calls(self) -> None:
        FakeAsyncOpenAI.closed = False
        settings = SimpleNamespace(
            deepseek_api_key="test-key",
            deepseek_base_url="https://example.test",
            deepseek_model="test-model",
        )
        tools = [{"type": "function", "function": {"name": "search_web", "parameters": {}}}]
        executor = mock.Mock(return_value='{"results": []}')

        with mock.patch.object(llm_json, "load_settings", return_value=settings):
            with mock.patch("openai.AsyncOpenAI", FakeAsyncOpenAI):
                result = asyncio.run(llm_json.call_json_model_with_tools(
                    role="tester",
                    payload={"task": "test"},
                    tools=tools,
                    tool_executor=executor,
                    max_tool_rounds=2,
                    fallback={"ok": False},
                ))

        self.assertEqual(result, {"ok": True})
        executor.assert_not_called()

    def test_call_json_model_with_tools_executes_tool_calls(self) -> None:
        tool_call_response = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(
                    content='{"result": "found data"}',
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            function=SimpleNamespace(
                                name="search_web",
                                arguments='{"query": "test"}',
                            ),
                        )
                    ],
                )
            )]
        )
        final_response = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(
                    content='{"answer": "based on search"}',
                    tool_calls=None,
                )
            )]
        )
        settings = SimpleNamespace(
            deepseek_api_key="test-key",
            deepseek_base_url="https://example.test",
            deepseek_model="test-model",
        )
        call_count = 0

        class FakeToolOpenAI:
            closed = False

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                type(self).closed = True

            @property
            def chat(self):
                async def create(**kwargs):
                    nonlocal call_count
                    call_count += 1
                    if call_count == 1:
                        return tool_call_response
                    return final_response

                return SimpleNamespace(completions=SimpleNamespace(create=create))

        tools = [{"type": "function", "function": {"name": "search_web", "parameters": {}}}]
        executor = mock.Mock(return_value='{"results": ["search result"]}')

        with mock.patch.object(llm_json, "load_settings", return_value=settings):
            with mock.patch("openai.AsyncOpenAI", FakeToolOpenAI):
                result = asyncio.run(llm_json.call_json_model_with_tools(
                    role="tester",
                    payload={"task": "test"},
                    tools=tools,
                    tool_executor=executor,
                    max_tool_rounds=2,
                    fallback={"ok": False},
                ))

        self.assertEqual(result, {"answer": "based on search"})
        executor.assert_called_once_with("search_web", {"query": "test"})

    def test_call_json_model_with_tools_executes_multiple_search_rounds(self) -> None:
        settings = SimpleNamespace(
            deepseek_api_key="test-key",
            deepseek_base_url="https://example.test",
            deepseek_model="test-model",
        )
        call_count = 0

        class MultiRoundOpenAI:
            closed = False

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                type(self).closed = True

            @property
            def chat(self):
                async def create(**kwargs):
                    nonlocal call_count
                    call_count += 1
                    if kwargs.get("tools") and call_count <= 2:
                        return SimpleNamespace(choices=[SimpleNamespace(
                            message=SimpleNamespace(
                                content='',
                                tool_calls=[SimpleNamespace(
                                    id=f"call_{call_count}",
                                    function=SimpleNamespace(name="search_web", arguments='{"query": "test"}'),
                                )],
                            )
                        )])
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content='{"ok": true}', tool_calls=None)
                    )])

                return SimpleNamespace(completions=SimpleNamespace(create=create))

        tools = [{"type": "function", "function": {"name": "search_web", "parameters": {}}}]

        def executor(name, args):
            return '{"results": []}'

        with mock.patch.object(llm_json, "load_settings", return_value=settings):
            with mock.patch("openai.AsyncOpenAI", MultiRoundOpenAI):
                result = asyncio.run(llm_json.call_json_model_with_tools(
                    role="tester",
                    payload={"task": "test"},
                    tools=tools,
                    tool_executor=executor,
                    max_tool_rounds=2,
                    fallback={"ok": False},
                ))

        self.assertEqual(result, {"ok": True})
        self.assertEqual(call_count, 3)


class ToolMessageLimitTests(unittest.TestCase):
    def test_apply_tool_message_limit_removes_oldest_pairs(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "r1"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "2"}]},
            {"role": "tool", "tool_call_id": "2", "content": "r2"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "3"}]},
            {"role": "tool", "tool_call_id": "3", "content": "r3"},
        ]
        result = llm_json._apply_tool_message_limit(messages, max_tool_rounds=2)
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 2)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "2")
        self.assertEqual(tool_msgs[1]["tool_call_id"], "3")

    def test_apply_tool_message_limit_no_trim_when_within_limit(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "r1"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "2"}]},
            {"role": "tool", "tool_call_id": "2", "content": "r2"},
        ]
        result = llm_json._apply_tool_message_limit(messages, max_tool_rounds=2)
        self.assertEqual(result, messages)

    def test_apply_tool_message_limit_skips_for_single_round(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "r1"},
        ]
        result = llm_json._apply_tool_message_limit(messages, max_tool_rounds=1)
        self.assertEqual(result, messages)

    def test_apply_tool_message_limit_preserves_content_only_assistant(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "thinking...", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "r1"},
            {"role": "assistant", "content": "result", "tool_calls": [{"id": "2"}]},
            {"role": "tool", "tool_call_id": "2", "content": "r2"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "3"}]},
            {"role": "tool", "tool_call_id": "3", "content": "r3"},
        ]
        result = llm_json._apply_tool_message_limit(messages, max_tool_rounds=2)
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 2)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "2")
        self.assertEqual(tool_msgs[1]["tool_call_id"], "3")


if __name__ == "__main__":
    unittest.main()
