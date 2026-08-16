from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.app.services import agent_harness, harness_state, study_tools


class AgentHarnessTests(unittest.TestCase):
    @staticmethod
    def _response_with_message(message):
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def test_read_tool_auto_executes(self) -> None:
        router = mock.Mock()
        router.model = "test-model"
        first = SimpleNamespace(
            content="",
            tool_calls=[
                SimpleNamespace(id="call_1", function=SimpleNamespace(name="list_docs", arguments="{}"))
            ],
        )
        second = SimpleNamespace(content="You have one doc.", tool_calls=[])
        router.client.chat.completions.create.side_effect = [
            self._response_with_message(first),
            self._response_with_message(second),
        ]

        with mock.patch("backend.app.services.study_tools.list_docs", return_value=[]):
            reply = agent_harness.run_agent_turn(
                router,
                messages=[{"role": "user", "content": "list docs"}],
                account_id="admin",
                user_dek=None,
                session_id="s1",
            )

        self.assertEqual(reply[0], "You have one doc.")
        self.assertEqual(router.client.chat.completions.create.call_count, 2)

    def test_mutation_tool_requires_confirmation(self) -> None:
        router = mock.Mock()
        router.model = "test-model"
        first = SimpleNamespace(
            content="",
            tool_calls=[
                SimpleNamespace(
                    id="call_1",
                    function=SimpleNamespace(name="create_lesson", arguments='{"topic":"graphs"}'),
                )
            ],
        )
        router.client.chat.completions.create.return_value = self._response_with_message(first)

        with tempfile.TemporaryDirectory() as td:
            state_path = Path(td) / "pending.json"
            with mock.patch.object(harness_state, "_STATE_PATH", state_path):
                reply = agent_harness.run_agent_turn(
                    router,
                    messages=[{"role": "user", "content": "start lesson on graphs"}],
                    account_id="admin",
                    user_dek=None,
                    session_id="s1",
                )

        self.assertIn("Please confirm", reply[0])
        self.assertIn("Action ID", reply[0])
        self.assertEqual(router.client.chat.completions.create.call_count, 1)

    def test_mutation_tool_requires_session_id(self) -> None:
        with mock.patch.object(agent_harness, "execute_tool") as execute_mock:
            with self.assertRaises(ValueError):
                agent_harness._tool_payload_or_confirmation(
                    name="create_lesson",
                    arguments={"topic": "graphs"},
                    account_id="admin",
                    user_dek=None,
                    session_id=None,
                )

        execute_mock.assert_not_called()

    def test_tool_registry_marks_mutations(self) -> None:
        self.assertEqual(study_tools.get_tool("list_docs").safety, "read")
        self.assertEqual(study_tools.get_tool("search_web").safety, "read")
        self.assertEqual(study_tools.get_tool("create_lesson").safety, "mutation")
        self.assertIn("start_qa_generation_job", {spec["function"]["name"] for spec in study_tools.build_tool_spec("admin")})
        self.assertIn("search_web", {spec["function"]["name"] for spec in study_tools.build_tool_spec("admin")})

    def test_search_web_consumes_turn_budget_and_blocks_excess(self) -> None:
        calls = [
            SimpleNamespace(id="call_1", function=SimpleNamespace(name="search_web", arguments='{"query":"Nvidia"}')),
            SimpleNamespace(id="call_2", function=SimpleNamespace(name="search_wikipedia", arguments='{"query":"Nvidia"}')),
            SimpleNamespace(id="call_3", function=SimpleNamespace(name="search_wikipedia", arguments='{"query":"Nvidia"}')),
        ]
        router = mock.Mock()
        router.model = "test-model"
        first = SimpleNamespace(content="", tool_calls=calls)
        second = SimpleNamespace(content="Search summary.", tool_calls=[])
        router.client.chat.completions.create.side_effect = [
            self._response_with_message(first),
            self._response_with_message(second),
        ]

        def fake_execute(name, arguments, account_id, user_dek):
            return json.dumps({"tool": name, "results": []})

        with mock.patch.object(agent_harness, "execute_tool", side_effect=fake_execute) as execute_mock:
            reply = agent_harness.run_agent_turn(
                router,
                messages=[{"role": "user", "content": "latest Nvidia news"}],
                account_id="admin",
                user_dek=None,
                session_id="s1",
            )

        self.assertEqual(reply[0], "Search summary.")
        self.assertEqual([call.args[0] for call in execute_mock.call_args_list], ["search_web", "search_wikipedia"])
        second_call_messages = router.client.chat.completions.create.call_args_list[1].kwargs["messages"]
        tool_payloads = [json.loads(message["content"]) for message in second_call_messages if message.get("role") == "tool"]
        self.assertEqual(tool_payloads[-1]["error"], "search_limit_exceeded")

    def test_stream_tool_turn_does_not_emit_partial_prefix(self) -> None:
        def chunk(*, content=None, tool_calls=None):
            return SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))]
            )

        tool_delta = SimpleNamespace(
            index=0,
            id="call_1",
            function=SimpleNamespace(name="list_docs", arguments="{}"),
        )
        router = mock.Mock()
        router.model = "test-model"
        prefix = "Great question! Let me look this up to give you a clear answer. "
        router.client.chat.completions.create.side_effect = [
            iter([chunk(content=prefix), chunk(tool_calls=[tool_delta])]),
            iter([chunk(content="Here is the answer.")]),
        ]

        with mock.patch("backend.app.services.study_tools.list_docs", return_value=[]):
            events = list(agent_harness.run_agent_turn_stream(
                router,
                messages=[{"role": "user", "content": "compare trees"}],
                account_id="admin",
                user_dek=None,
                session_id="s1",
            ))

        tokens = [data for event, data in events if event == "token"]
        combined = "".join(tokens)
        self.assertNotIn("Great question!", combined)
        self.assertIn("Here is the answer.", combined)

    def test_stream_tool_turn_falls_back_to_doc_excerpt_when_followup_empty(self) -> None:
        def chunk(*, content=None, tool_calls=None):
            return SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))]
            )

        tool_delta = SimpleNamespace(
            index=0,
            id="call_1",
            function=SimpleNamespace(name="open_doc_excerpt", arguments='{"doc_name":"AB tree.md"}'),
        )
        router = mock.Mock()
        router.model = "test-model"
        router.client.chat.completions.create.side_effect = [
            iter([chunk(tool_calls=[tool_delta])]),
            iter([]),
        ]

        payload = json.dumps({
            "name": "AB tree.md",
            "excerpt": "#CS2040S #Uni\nFor a (A< B) tree the A is the lower and B is the upper.",
            "chunk_count": 1,
        })
        with mock.patch.object(agent_harness, "execute_tool", return_value=payload):
            events = list(agent_harness.run_agent_turn_stream(
                router,
                messages=[{"role": "user", "content": 'what is in @"AB tree.md"'}],
                account_id="admin",
                user_dek=None,
                session_id="s1",
            ))

        tokens = [data for event, data in events if event == "token"]
        combined = "".join(tokens)
        self.assertIn("Here is what I found in `AB tree.md`", combined)
        self.assertIn("For a (A< B) tree", combined)


if __name__ == "__main__":
    unittest.main()
