from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
import math
import re
import shutil
import textwrap
import time
from typing import Any, Callable, Literal

from openai import OpenAI

from engine.quiz.quiz_backend import (
    QuizBuildResponse,
    QuizGradeRequest,
    QuizGradeResponse,
    QuizSubmissionAnswer,
    grade_quiz,
)
from engine.quiz.model_setup import MODEL_NAME
from engine.shared.context_builder import HybridContextBuilder

ROUTER_CONNECT_TIMEOUT_SEC = 3.0
ROUTER_ROUTE_TIMEOUTS_SEC = (3.0, 5.0)
ROUTER_TOOL_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "route_quiz_intent",
            "description": "Route user input to exactly one quiz tool command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "enum": [
                            "ask",
                            "review",
                            "explain",
                            "dashboard",
                            "next",
                            "back",
                            "skip",
                            "submit",
                            "quit",
                            "answer",
                        ],
                    },
                    "ask_text": {"type": ["string", "null"]},
                    "answer_text": {"type": ["string", "null"]},
                    "question_index": {"type": ["integer", "null"], "minimum": 0},
                },
                "required": ["tool"],
                "additionalProperties": False,
            },
        },
    }
]


@dataclass
class QuizSession:
    quiz: QuizBuildResponse
    current_index: int = 0
    answers: dict[str, str] = field(default_factory=dict)
    memory: "TempVectorMemory" = field(default_factory=lambda: TempVectorMemory())

    def current_question(self):
        return self.quiz.questions[self.current_index]

    def is_complete(self) -> bool:
        return len(self.answers) == len(self.quiz.questions)


@dataclass
class QuizIntent:
    tool: str
    ask_text: str | None = None
    answer_text: str | None = None
    question_index: int | None = None
    route_mode: str = "unknown"


@dataclass
class ToolRequest:
    raw: str
    routed: QuizIntent
    at_last: bool


@dataclass
class ToolResponse:
    status: Literal["continue", "submit", "quit", "unknown"]


@dataclass
class RouterTelemetry:
    total_routes: int = 0
    tool_call_routes: int = 0
    fallback_json_routes: int = 0
    failed_routes: int = 0
    total_route_ms: float = 0.0
    last_mode: str = "none"
    last_latency_ms: float = 0.0


class QuizIntentRouter:
    def __init__(self, *, api_key: str, model: str, base_url: str = "https://api.deepseek.com") -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.connected = False
        self.telemetry = RouterTelemetry()
        self.router_context_builder = HybridContextBuilder(
            system_preamble=(
                "You are a quiz session intent router. Select exactly one tool. "
                "Prefer ask for clarifications/definitions. Use explain only for mistake-analysis requests."
            ),
            policy_payload={
                "role": "quiz_router",
                "policy_version": "v2",
                "tools": ["ask", "review", "explain", "dashboard", "next", "back", "skip", "submit", "quit", "answer"],
                "output": "tool_selection",
            },
        )
        self.tutor_context_builder = HybridContextBuilder(
            system_preamble="You are a quiz tutor. Answer the student's question directly and clearly.",
            policy_payload={
                "role": "quiz_tutor",
                "policy_version": "v2",
                "requirements": [
                    "concise_3_to_6_sentences",
                    "beginner_friendly",
                    "accurate",
                    "definition_first_when_requested",
                ],
            },
        )
        self.mistake_context_builder = HybridContextBuilder(
            system_preamble="You are a quiz tutor focused on explaining mistakes clearly and briefly.",
            policy_payload={
                "role": "mistake_explainer",
                "policy_version": "v2",
                "requirements": [
                    "state_what_was_wrong",
                    "state_correct_concept",
                    "give_one_fix_tip_per_mistake",
                ],
            },
        )

    def check_connection(self) -> bool:
        try:
            _ = self.client.models.list(timeout=ROUTER_CONNECT_TIMEOUT_SEC)
            self.connected = True
            return True
        except Exception:
            self.connected = False
            return False

    def route(self, *, raw: str, quiz: QuizBuildResponse, current_index: int, at_last: bool) -> QuizIntent | None:
        if not quiz.questions or current_index < 0 or current_index >= len(quiz.questions):
            return None
        current_question = quiz.questions[current_index].question
        prompt = _build_router_prompt(
            raw=raw,
            current_question=current_question,
            total_questions=len(quiz.questions),
            current_index=current_index,
            at_last=at_last,
        )
        try:
            payload = None
            mode = "none"
            latency_ms = 0.0
            for timeout in ROUTER_ROUTE_TIMEOUTS_SEC:
                payload, mode, latency_ms = self._route_once(
                    raw=raw,
                    current_question=current_question,
                    total_questions=len(quiz.questions),
                    current_index=current_index,
                    at_last=at_last,
                    fallback_prompt=prompt,
                    timeout=timeout,
                )
                if payload is not None:
                    break
            if payload is None:
                self._record_telemetry(mode="failed", latency_ms=latency_ms, ok=False)
                return None
            self._record_telemetry(mode=mode, latency_ms=latency_ms, ok=True)
            tool = str(payload.get("tool", "")).strip().lower()
            if tool not in {"ask", "review", "explain", "next", "back", "skip", "submit", "quit", "answer", "dashboard"}:
                return None
            ask_text = payload.get("ask_text")
            answer_text = payload.get("answer_text")
            qidx = payload.get("question_index")
            if not isinstance(qidx, int):
                qidx = None
            return QuizIntent(
                tool=f"/{tool}",
                ask_text=ask_text if isinstance(ask_text, str) else None,
                answer_text=answer_text if isinstance(answer_text, str) else None,
                question_index=qidx,
                route_mode=mode,
            )
        except Exception:
            self._record_telemetry(mode="failed", latency_ms=0.0, ok=False)
            return None

    def _route_once(
        self,
        *,
        raw: str,
        current_question: str,
        total_questions: int,
        current_index: int,
        at_last: bool,
        fallback_prompt: str,
        timeout: float,
    ) -> tuple[dict[str, Any] | None, str, float]:
        messages = self.router_context_builder.build_messages(
            user_payload={
                "input": raw,
                "current_question": current_question,
                "question_position": {"index": current_index, "total": total_questions},
                "at_last_question": at_last,
            }
        )
        try:
            started = _now_ms()
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=ROUTER_TOOL_SPEC,
                tool_choice={"type": "function", "function": {"name": "route_quiz_intent"}},
                stream=False,
                timeout=timeout,
            )
            latency = _now_ms() - started
            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                args_text = tool_calls[0].function.arguments or "{}"
                return json.loads(args_text), "tool_call", latency
        except Exception:
            pass

        # Compact fallback path for providers/models that ignore tool-calling.
        started = _now_ms()
        fallback_messages = self.router_context_builder.build_messages(
            user_payload={
                "fallback_instruction": "Return strict JSON object for tool routing.",
                "prompt": fallback_prompt,
            }
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=fallback_messages,
            response_format={"type": "json_object"},
            stream=False,
            timeout=timeout,
        )
        latency = _now_ms() - started
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return None, "fallback_json", latency
        try:
            return json.loads(content), "fallback_json", latency
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None, "fallback_json", latency
            try:
                return json.loads(content[start : end + 1]), "fallback_json", latency
            except json.JSONDecodeError:
                return None, "fallback_json", latency

    def _record_telemetry(self, *, mode: str, latency_ms: float, ok: bool) -> None:
        self.telemetry.total_routes += 1
        self.telemetry.total_route_ms += max(0.0, float(latency_ms))
        self.telemetry.last_mode = mode
        self.telemetry.last_latency_ms = max(0.0, float(latency_ms))
        if mode == "tool_call":
            self.telemetry.tool_call_routes += 1
        elif mode == "fallback_json":
            self.telemetry.fallback_json_routes += 1
        if not ok:
            self.telemetry.failed_routes += 1

    def summary(self) -> str:
        total = max(1, self.telemetry.total_routes)
        avg_ms = self.telemetry.total_route_ms / total
        return (
            f"routes={self.telemetry.total_routes}, "
            f"tool_call={self.telemetry.tool_call_routes}, "
            f"fallback_json={self.telemetry.fallback_json_routes}, "
            f"failed={self.telemetry.failed_routes}, "
            f"avg_ms={avg_ms:.1f}, "
            f"last={self.telemetry.last_mode}/{self.telemetry.last_latency_ms:.1f}ms"
        )

    def telemetry_snapshot(self) -> dict[str, Any]:
        total = max(1, self.telemetry.total_routes)
        avg_ms = self.telemetry.total_route_ms / total
        return {
            "routes": int(self.telemetry.total_routes),
            "tool_call": int(self.telemetry.tool_call_routes),
            "fallback_json": int(self.telemetry.fallback_json_routes),
            "failed": int(self.telemetry.failed_routes),
            "avg_ms": round(avg_ms, 3),
            "last_mode": self.telemetry.last_mode,
            "last_latency_ms": round(self.telemetry.last_latency_ms, 3),
        }

    def reset_telemetry(self) -> None:
        self.telemetry = RouterTelemetry()


def run_quiz_session(
    quiz: QuizBuildResponse,
    intent_router: QuizIntentRouter | None = None,
    dashboard_handler: Callable[[], str] | None = None,
) -> QuizGradeResponse:
    session = QuizSession(quiz=quiz)
    if intent_router is not None:
        intent_router.reset_telemetry()
    tool_registry = _build_tool_registry(session=session, router=intent_router, dashboard_handler=dashboard_handler)
    def _print_router_summary() -> None:
        if intent_router is not None:
            print(f"[Router] Summary: {intent_router.summary()}")
    print(f"\nQuiz ready: {quiz.quiz_id}")
    print("Commands: /ask <question> /review /explain /dashboard /next /back /skip /submit /quit\n")
    if intent_router is None:
        print("[Router] LLM router: offline (fallback mode)\n")
    else:
        is_connected = intent_router.check_connection()
        status = "connected" if is_connected else "unreachable (fallback mode)"
        print(f"[Router] LLM router: {status}\n")
    last_answered_notice_shown = False

    while True: #while there is still questions
        q = session.current_question()
        at_last = session.current_index == len(session.quiz.questions) - 1
        existing = session.answers.get(q.question_id)
        if at_last and last_answered_notice_shown:
            raw = input("End of quiz. Use /review, /submit, /back, or /quit: ").strip() #last one should be different
        else:
            print(f"Q{session.current_index + 1}/{len(session.quiz.questions)} [{q.difficulty}] {q.question}")
            if q.question_mode == "mcq" and q.options: #mcq formating
                for idx, option in enumerate(q.options):
                    label = chr(ord("A") + idx)
                    print(f"  {label}. {option}")
            if existing: #open-ended formating
                print(f"Current answer: {existing}")
            raw = input("Your answer (or command): ").strip()
        routed = _route_input(
            raw=raw,
            quiz=session.quiz,
            current_index=session.current_index,
            at_last=at_last,
            has_existing_answer=existing is not None,
            router=intent_router,
        )
        command = routed.tool
        print(f"[Router] Action: {command}") #for just context
        if routed.route_mode in {"tool_call", "fallback_json"} and intent_router is not None:
            print(f"[Router] Route mode: {routed.route_mode} ({intent_router.telemetry.last_latency_ms:.1f} ms)")
        else:
            print(f"[Router] Route mode: {routed.route_mode}")
        q = session.current_question()

        if command in tool_registry:
            tool_response = tool_registry[command](ToolRequest(raw=raw, routed=routed, at_last=at_last))
            if tool_response.status == "quit":
                _print_router_summary()
                raise RuntimeError("Quiz session quit by user.")
            if tool_response.status == "submit":
                _print_router_summary()
                return _grade(session)
            if tool_response.status in {"continue", "unknown"}:
                if command in {"/back", "/next", "/skip"} and tool_response.status == "continue":
                    if at_last and command in {"/next", "/skip"}:
                        last_answered_notice_shown = True
                    else:
                        last_answered_notice_shown = False
                continue

        if command == "/unknown":
            print("Unknown command. Use /ask, /review, /dashboard, /submit, /back, /next, /skip, or /quit.\n")
            continue
        answer_raw = routed.answer_text if command == "/answer" and routed.answer_text else raw
        if not answer_raw:
            print("Answer cannot be empty.\n")
            continue

        session.answers[q.question_id] = answer_raw
        session.memory.add(
            text=(
                f"Question: {q.question}\n"
                f"User answer: {answer_raw}\n"
                f"Expected answer: {q.expected_answer}\n"
                f"Difficulty: {q.difficulty}"
            )
        )
        if session.current_index < len(session.quiz.questions) - 1: #to keep track of number of questions
            session.current_index += 1
            last_answered_notice_shown = False
        else:
            print("\nYou are on the last question.")
            print("Use /review to discuss your current answers before final submit.")
            print("Available commands now: /submit (finish) or /quit (exit).")
            print("You can also overwrite your answer by typing a new answer.\n")
            last_answered_notice_shown = True


def _build_tool_registry(
    *,
    session: QuizSession,
    router: QuizIntentRouter | None,
    dashboard_handler: Callable[[], str] | None,
) -> dict[str, Callable[[ToolRequest], ToolResponse]]:
    def _handle_quit(_: ToolRequest) -> ToolResponse:
        return ToolResponse(status="quit")

    def _handle_submit(_: ToolRequest) -> ToolResponse:
        return ToolResponse(status="submit")

    def _handle_review(_: ToolRequest) -> ToolResponse:
        _review(session)
        print()
        return ToolResponse(status="continue")

    def _handle_dashboard(_: ToolRequest) -> ToolResponse:
        if dashboard_handler is None:
            print("Dashboard is not available in this session.\n")
        else:
            answered_now = len(session.answers)
            total_now = len(session.quiz.questions)
            print(dashboard_handler())
            print(
                "Current quiz progress: "
                f"{answered_now}/{total_now} answered, "
                f"{max(0, total_now - answered_now)} remaining."
            )
            print()
        return ToolResponse(status="continue")

    def _handle_explain(req: ToolRequest) -> ToolResponse:
        _explain_mistakes(session=session, router=router, user_prompt=req.raw)
        print()
        return ToolResponse(status="continue")

    def _handle_ask(req: ToolRequest) -> ToolResponse:
        q = session.current_question()
        ask_text = req.routed.ask_text or _extract_ask_text(req.raw)
        if not ask_text:
            print("Usage: /ask <question about the current quiz question>\n")
            return ToolResponse(status="continue")
        target_index = req.routed.question_index
        if target_index is None:
            target_index = _extract_question_reference(raw=req.raw, total=len(session.quiz.questions))
        target_q = session.quiz.questions[target_index] if target_index is not None else q
        memory_context = "\n\n".join(entry.text for entry in session.memory.search(query=ask_text, top_k=4))
        answer = _ask_with_llm(
            router=router,
            ask_text=ask_text,
            question=target_q.question,
            expected_answer=target_q.expected_answer,
            source_context=target_q.source_context,
            memory_context=memory_context,
        ) or _answer_about_current_question(
            target_q.question,
            target_q.expected_answer,
            target_q.source_context,
            ask_text,
        )
        _print_wrapped(answer)
        print()
        return ToolResponse(status="continue")

    def _handle_back(_: ToolRequest) -> ToolResponse:
        session.current_index = max(0, session.current_index - 1)
        return ToolResponse(status="continue")

    def _handle_next(req: ToolRequest) -> ToolResponse:
        if req.at_last:
            print("You are at the last question. Use /submit to finish or /quit to exit.\n")
            return ToolResponse(status="continue")
        session.current_index = min(len(session.quiz.questions) - 1, session.current_index + 1)
        return ToolResponse(status="continue")

    def _handle_skip(req: ToolRequest) -> ToolResponse:
        return _handle_next(req)

    return {
        "/quit": _handle_quit,
        "/submit": _handle_submit,
        "/review": _handle_review,
        "/dashboard": _handle_dashboard,
        "/explain": _handle_explain,
        "/ask": _handle_ask,
        "/back": _handle_back,
        "/next": _handle_next,
        "/skip": _handle_skip,
    }


def _grade(session: QuizSession) -> QuizGradeResponse:
    req = QuizGradeRequest(
        quiz_id=session.quiz.quiz_id,
        answers=[
            QuizSubmissionAnswer(question_id=qid, answer=ans)
            for qid, ans in session.answers.items()
        ],
    )
    result = grade_quiz(quiz=session.quiz, request=req)
    print("\n=== Result ===")
    print(f"Score: {result.correct_answers}/{result.total_questions} ({result.score_percent}%)")
    for item in result.results:
        status = "OK" if item.is_correct else "NO"
        print(f"- {item.question_id}: {status} ({item.score})")
    print()
    return result


def _review(session: QuizSession) -> None: #grade and explain what is going on
    if not session.answers:
        print("No answers yet. Answer at least one question, then run /review.")
        return
    req = QuizGradeRequest(
        quiz_id=session.quiz.quiz_id,
        answers=[
            QuizSubmissionAnswer(question_id=qid, answer=ans)
            for qid, ans in session.answers.items()
        ],
    )
    preview = grade_quiz(quiz=session.quiz, request=req)
    print("\n=== Review (Not Submitted) ===")
    print(f"Answered: {preview.answered_questions}/{preview.total_questions}")
    for q in session.quiz.questions:
        submitted = session.answers.get(q.question_id)
        if submitted is None:
            print(f"- {q.question_id}: unanswered")
            continue
        graded = next((r for r in preview.results if r.question_id == q.question_id), None)
        if graded is None:
            print(f"- {q.question_id}: answered")
            continue
        status = "likely correct" if graded.is_correct else "needs work"
        print(f"- {q.question_id}: {status} (score {graded.score})")
        print(f"  Your answer: {graded.submitted_answer}")
        if q.question_mode == "mcq" and q.options and q.correct_option_index is not None:
            label = chr(ord("A") + q.correct_option_index)
            print(f"  Expected: {label}. {q.options[q.correct_option_index]}")
        else:
            print(f"  Expected: {graded.expected_answer}")


def _explain_mistakes(*, session: QuizSession, router: QuizIntentRouter | None, user_prompt: str) -> None:
    req = QuizGradeRequest(
        quiz_id=session.quiz.quiz_id,
        answers=[QuizSubmissionAnswer(question_id=qid, answer=ans) for qid, ans in session.answers.items()],
    )
    preview = grade_quiz(quiz=session.quiz, request=req)
    wrong = [r for r in preview.results if not r.is_correct]
    if not wrong:
        print("You have no mistakes so far. Nice work.")
        return
    lines = []
    for item in wrong:
        q = next((qq for qq in session.quiz.questions if qq.question_id == item.question_id), None)
        q_text = q.question if q else item.question_id
        lines.append(
            f"- {item.question_id}\nQuestion: {q_text}\nYour answer: {item.submitted_answer}\nExpected: {item.expected_answer}"
        )
    retrieval_query = f"{user_prompt}\n" + "\n".join(lines)
    retrieved = session.memory.search(query=retrieval_query, top_k=6)
    context_block = "\n\n".join(entry.text for entry in retrieved)

    if router is not None and os.environ.get("OBITAL_ALLOW_ONLINE_TUTOR", "").strip().lower() in {"1", "true", "yes"}:
        try:
            messages = router.mistake_context_builder.build_messages(
                user_payload={
                    "user_prompt": user_prompt,
                    "mistakes": lines,
                    "recent_context": context_block,
                }
            )
            resp = router.client.chat.completions.create(
                model=router.model,
                messages=messages,
                stream=False,
                timeout=8.0,
            )
            content = (resp.choices[0].message.content or "").strip()
            if content:
                _print_wrapped(content)
                return
        except Exception:
            pass

    print("Mistake breakdown:")
    for item in wrong:
        print(f"- {item.question_id}: your answer missed key points.")
        print(f"  Expected: {item.expected_answer}")
        print("  Tip: compare your answer to expected keywords and restate in one clear sentence.")


def _ask_with_llm(
    *,
    router: QuizIntentRouter | None,
    ask_text: str,
    question: str,
    expected_answer: str,
    source_context: str | None,
    memory_context: str,
) -> str | None:
    if router is None:
        return None
    if os.environ.get("OBITAL_ALLOW_ONLINE_TUTOR", "").strip().lower() not in {"1", "true", "yes"}:
        return None
    try:
        messages = router.tutor_context_builder.build_messages(
            user_payload={
                "student_ask": ask_text,
                "current_question": question,
                "expected_answer_reference": expected_answer,
                "source_context": source_context or "",
                "recent_memory_context": memory_context,
            }
        )
        resp = router.client.chat.completions.create(
            model=router.model,
            messages=messages,
            stream=False,
            timeout=8.0,
        )
        content = (resp.choices[0].message.content or "").strip()
        return content or None
    except Exception:
        return None


def _answer_about_current_question(
    question: str,
    expected_answer: str,
    source_context: str | None,
    ask_text: str,
) -> str:
    keywords = _extract_keywords(ask_text)
    corpus = "\n".join([question, source_context or "", expected_answer]).strip()
    if not corpus:
        return "I do not have enough context for that question."
    if not keywords:
        return (
            "Try asking with keywords from the quiz question. "
            "I can explain terms, constraints, or what the question is asking."
        )

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", corpus) if s.strip()]
    ranked = sorted(
        sentences,
        key=lambda s: sum(1 for kw in keywords if kw in s.lower()),
        reverse=True,
    )
    best = [s for s in ranked if any(kw in s.lower() for kw in keywords)][:2]
    if not best:
        return (
            "I cannot find that detail in this question context. "
            "Try a narrower ask, or answer and I will grade it."
        )
    return "Clarification:\n- " + "\n- ".join(best)


def _extract_keywords(text: str) -> list[str]: #fallback, api should do most of the work
    stopwords = {
        "what",
        "which",
        "when",
        "where",
        "who",
        "why",
        "how",
        "is",
        "are",
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "on",
        "for",
        "and",
        "or",
        "it",
        "this",
        "that",
        "do",
        "does",
        "can",
        "could",
    }
    tokens = re.findall(r"[a-z0-9]{3,}", text.lower())
    return [t for t in tokens if t not in stopwords]


def _extract_ask_text(raw: str) -> str:
    text = raw.strip()
    if text.lower().startswith("/ask"):
        return text[4:].strip()
    lowered = text.lower()
    if lowered.startswith("ask "):
        return text[4:].strip()
    return text


def _extract_question_reference(*, raw: str, total: int) -> int | None:
    match = re.search(r"\bq(?:uestion)?\s*(\d{1,2})\b", raw.lower())
    if not match:
        return None
    q_num = int(match.group(1))
    if q_num < 1 or q_num > total:
        return None
    return q_num - 1


def _detect_command(*, raw: str, at_last: bool, has_existing_answer: bool) -> str | None:
    text = raw.strip()
    if not text:
        return None
    lowered = text.lower()

    # Exact command path remains supported.
    if lowered in {"/quit", "/submit", "/review", "/explain", "/dashboard", "/back", "/next", "/skip"}:
        return lowered
    if lowered.startswith("/ask"):
        return "/ask"

    # Natural-language intent mapping — use word-boundary checks to avoid
    # matching substrings inside answers (e.g. "I will quit" in an answer).
    def _has_word(token: str) -> bool:
        return bool(re.search(rf"\b{token}\b", lowered))

    if any(_has_word(t) for t in {"quit", "exit", "leave"}):
        return "/quit"
    if any(_has_word(t) for t in {"submit", "finish", "done"}):
        return "/submit"
    if any(_has_word(t) for t in {"review", "analyse", "analyze", "analysis"}) or "explain my answer" in lowered:
        return "/review"
    if any(_has_word(t) for t in {"dashboard", "stats", "progress", "performance"}) or "my stats" in lowered or "show stats" in lowered or "my progress" in lowered or "show progress" in lowered:
        return "/dashboard"
    if any(_has_word(t) for t in {"mistakes", "wrong"}) or "explain my mistakes" in lowered or "where i went wrong" in lowered or "why wrong" in lowered:
        return "/explain"
    if any(_has_word(t) for t in {"back", "previous", "prev"}):
        return "/back"
    if _has_word("skip"):
        return "/skip"
    if _has_word("next"):
        return "/next"
    if lowered.startswith("ask ") or lowered.startswith("question "):
        return "/ask"
    if lowered.startswith("can you explain") or lowered.startswith("could you explain"):
        return "/ask"
    if any(_has_word(t) for t in {"help me understand", "understand q", "explain q", "explain question"}):
        return "/ask"
    if "explain" in lowered and any(_has_word(t) for t in {"this", "that", "question", "q"}):
        return "/ask"
    if any(_has_word(t) for t in {"rephrase", "paraphrase", "simplify", "reword"}):
        if any(_has_word(t) for t in {"this", "question", "q"}):
            return "/ask"
    if "what is this asking" in lowered or "what does this mean" in lowered:
        return "/ask"

    # At end-of-quiz prompt, treat explanation-style text as review request.
    if at_last and has_existing_answer and any(
        _has_word(t) for t in {"explain", "analysis", "analyze", "analyse", "discuss"}
    ):
        if _has_word("mistake") or _has_word("wrong"):
            return "/explain"
        return "/review"

    return None


def _route_input(
    *,
    raw: str,
    quiz: QuizBuildResponse,
    current_index: int,
    at_last: bool,
    has_existing_answer: bool,
    router: QuizIntentRouter | None,
) -> QuizIntent:
    slash = _slash_command(raw)
    if slash is not None:
        return QuizIntent(tool=slash, route_mode="slash")
    if raw.strip().startswith("/"):
        return QuizIntent(tool="/unknown", route_mode="slash_unknown")
    # Agentic-first mode: let the LLM decide intent for natural language.
    if router is not None:
        print("[Router] Sending input to LLM router...")
        routed = router.route(raw=raw, quiz=quiz, current_index=current_index, at_last=at_last)
        if routed is not None:
            if routed.question_index is not None:
                if routed.question_index < 0 or routed.question_index >= len(quiz.questions):
                    routed.question_index = None
            return routed
        print("[Router] LLM routing unavailable, falling back to local intent rules.")
    command = _detect_command(raw=raw, at_last=at_last, has_existing_answer=has_existing_answer)
    if command is not None:
        if command == "/ask":
            return QuizIntent(tool=command, ask_text=_extract_ask_text(raw), route_mode="local_fallback")
        return QuizIntent(tool=command, route_mode="local_fallback")
    if _looks_like_question(raw):
        return QuizIntent(tool="/ask", ask_text=raw.strip(), route_mode="heuristic_question")
    return QuizIntent(tool="/answer", answer_text=raw, route_mode="default_answer")


def _slash_command(raw: str) -> str | None:
    lowered = raw.strip().lower()
    if lowered in {"/quit", "/submit", "/review", "/explain", "/dashboard", "/back", "/next", "/skip"}:
        return lowered
    if lowered.startswith("/ask"):
        return "/ask"
    return None


def _build_router_prompt(
    *,
    raw: str,
    current_question: str,
    total_questions: int,
    current_index: int,
    at_last: bool,
) -> str:
    return (
        "You are a quiz session controller. Map user input to one tool.\n"
        "Tools: ask, review, explain, dashboard, next, back, skip, submit, quit, answer.\n"
        "Rules:\n"
        "- Keep slash command semantics if user clearly asks one.\n"
        "- If user asks explanation/discussion/help, use ask or review.\n"
        "- If user references qN, set question_index to N-1.\n"
        "- Use answer only when user is clearly giving/updating an answer.\n"
        f"- At last question state: {at_last}.\n"
        f"- Current question index: {current_index}.\n"
        f"- Current question ({current_index + 1}/{total_questions}): {current_question}\n"
        "Return strict JSON only:\n"
        '{"tool":"ask|review|explain|dashboard|next|back|skip|submit|quit|answer","ask_text":string|null,"answer_text":string|null,"question_index":number|null}\n'
        f"User input: {raw}"
    )


def _looks_like_question(raw: str) -> bool:
    text = raw.strip().lower()
    if not text:
        return False
    if text.endswith("?"):
        return True
    starters = (
        "what ",
        "why ",
        "how ",
        "when ",
        "where ",
        "who ",
        "can you ",
        "could you ",
        "help me ",
        "explain ",
    )
    return text.startswith(starters)


def _print_wrapped(text: str) -> None:
    width = max(60, min(120, shutil.get_terminal_size((100, 20)).columns - 2))
    lines = text.splitlines() or [text]
    for line in lines:
        if not line.strip():
            print()
            continue
        print(textwrap.fill(line, width=width))


@dataclass
class MemoryEntry:
    text: str
    vector: list[float] | None
    index: int


class TempVectorMemory:
    def __init__(self) -> None:
        self._entries: list[MemoryEntry] = []
        self._embedder = None
        self._embed_dim = 96
        self._failed = False

    def add(self, *, text: str) -> None:
        # Store raw text immediately; embed lazily only when retrieval is requested.
        self._entries.append(MemoryEntry(text=text, vector=None, index=len(self._entries)))

    def search(self, *, query: str, top_k: int = 5) -> list[MemoryEntry]:
        if not self._entries:
            return []
        qvec = self._embed(query)
        scored: list[tuple[float, MemoryEntry]] = []
        total = len(self._entries)
        for e in self._entries:
            if e.vector is None:
                e.vector = self._embed(e.text)
            recency = (e.index + 1) / total
            sim = _cosine_similarity(qvec, e.vector) if qvec is not None and e.vector is not None else _lexical_overlap(query, e.text)
            score = 0.7 * sim + 0.3 * recency
            scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def _embed(self, text: str) -> list[float] | None:
        if self._failed:
            return None
        if self._embedder is None:
            try:
                from engine.shared.embedding_loader import build_embed_fn

                self._embedder, self._embed_dim, _fallback = build_embed_fn(MODEL_NAME)
            except Exception:
                self._failed = True
                return None
        vec = self._embedder(text)
        return vec if vec is not None else None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, (dot / denom + 1.0) / 2.0))


def _lexical_overlap(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]+", a.lower()))
    tb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not ta or not tb:
        return 0.0
    inter = ta.intersection(tb)
    return len(inter) / max(1, len(ta))


def _now_ms() -> float:
    return time.perf_counter() * 1000.0
