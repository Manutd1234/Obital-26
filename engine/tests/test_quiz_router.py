from __future__ import annotations

import unittest

from engine.quiz.quiz_backend import QuizBuildResponse, QuizQuestion
from engine.quiz.quiz_tui import (
    ROUTER_CONNECT_TIMEOUT_SEC,
    QuizIntentRouter,
    _route_input,
)


class _FakeRouter:
    def route(self, **kwargs):
        return None


class QuizRouterTests(unittest.TestCase):
    def _quiz(self) -> QuizBuildResponse:
        return QuizBuildResponse(
            quiz_id="quiz_1",
            questions=[
                QuizQuestion(
                    question_id="q1",
                    topic_id="t1",
                    topic_title="Trees",
                    question="What is a B-tree?",
                    expected_answer="Balanced search tree.",
                    difficulty="medium",
                )
            ],
        )

    def test_route_input_falls_back_to_ask_for_question_like_text(self) -> None:
        routed = _route_input(
            raw="what is a b tree",
            quiz=self._quiz(),
            current_index=0,
            at_last=False,
            has_existing_answer=False,
            router=_FakeRouter(),
        )
        self.assertEqual(routed.tool, "/ask")

    def test_route_input_unknown_slash(self) -> None:
        routed = _route_input(
            raw="/something",
            quiz=self._quiz(),
            current_index=0,
            at_last=False,
            has_existing_answer=False,
            router=None,
        )
        self.assertEqual(routed.tool, "/unknown")

    def test_route_input_explain_mistakes_maps_to_explain(self) -> None:
        routed = _route_input(
            raw="can you explain my mistakes",
            quiz=self._quiz(),
            current_index=0,
            at_last=True,
            has_existing_answer=True,
            router=None,
        )
        self.assertEqual(routed.tool, "/explain")

    def test_check_connection_uses_timeout(self) -> None:
        router = QuizIntentRouter(api_key="x", model="y", base_url="https://api.deepseek.com")

        class _Models:
            def __init__(self):
                self.seen_timeout = None

            def list(self, **kwargs):
                self.seen_timeout = kwargs.get("timeout")
                return type("R", (), {"data": []})()

        models = _Models()
        router.client = type("C", (), {"models": models})()
        ok = router.check_connection()
        self.assertTrue(ok)
        self.assertEqual(models.seen_timeout, ROUTER_CONNECT_TIMEOUT_SEC)


if __name__ == "__main__":
    unittest.main()
