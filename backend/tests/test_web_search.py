from __future__ import annotations

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from backend.app.services import study_tools, web_search


class WebSearchServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        web_search.clear_exa_client_cache()

    def test_exa_client_reads_api_key_from_environment(self) -> None:
        fake_exa_module = SimpleNamespace(Exa=mock.Mock())
        with mock.patch.dict(os.environ, {"EXA_API_KEY": "test-key"}, clear=False):
            with mock.patch.dict(sys.modules, {"exa_py": fake_exa_module}):
                client = web_search._load_exa_client()

        fake_exa_module.Exa.assert_called_once_with("test-key")
        self.assertIs(client, fake_exa_module.Exa.return_value)

    def test_missing_api_key_returns_configuration_error(self) -> None:
        with mock.patch.dict(os.environ, {"EXA_API_KEY": ""}, clear=False):
            payload = web_search.search_exa("latest Nvidia news")

        self.assertEqual(payload["error"], "exa_not_configured")
        self.assertNotIn("test-key", json.dumps(payload))

    def test_search_web_combines_web_before_wikipedia(self) -> None:
        exa_payload = {
            "results": [
                {"source": "web", "rank": 1, "title": "Web hit", "url": "https://example.com", "snippet": "web"}
            ]
        }
        wiki_payload = {
            "results": [
                {"source": "wikipedia", "rank": 1, "title": "Wiki hit", "url": "https://wikipedia.org", "snippet": "wiki"}
            ]
        }

        with mock.patch.object(web_search, "search_exa", return_value=exa_payload) as exa_mock:
            with mock.patch.object(web_search, "search_wikipedia", return_value=wiki_payload) as wiki_mock:
                payload = web_search.search_web_and_wikipedia("Nvidia", num_results=5)

        exa_mock.assert_called_once()
        wiki_mock.assert_called_once()
        self.assertEqual([item["source"] for item in payload["results"]], ["web", "wikipedia"])

    def test_study_tool_registry_exposes_search_web_as_read_tool(self) -> None:
        names = {spec["function"]["name"] for spec in study_tools.build_tool_spec("admin")}

        self.assertIn("search_web", names)
        self.assertEqual(study_tools.get_tool("search_web").safety, "read")

    def test_execute_search_web_returns_normalized_json(self) -> None:
        payload = {
            "query": "Nvidia",
            "provider_requests": 2,
            "results": [{"source": "web", "rank": 1, "title": "Hit", "url": "https://example.com", "snippet": "x"}],
        }
        with mock.patch.object(web_search, "search_web_and_wikipedia", return_value=payload):
            raw = study_tools.execute_tool("search_web", {"query": "Nvidia"}, "admin", None)

        self.assertEqual(json.loads(raw), payload)


class LiveExaConnectionTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv("EXA_API_KEY"), "EXA_API_KEY is not configured")
    def test_live_exa_connection_smoke(self) -> None:
        web_search.clear_exa_client_cache()
        payload = web_search.search_exa("Nvidia", num_results=1)

        self.assertNotIn("error", payload)
        self.assertIsInstance(payload.get("results"), list)
