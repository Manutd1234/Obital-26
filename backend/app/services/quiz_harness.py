# Authored by Noah — https://github.com/noah-e1528170
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from typing import Any, Callable

from engine.quiz.quiz_backend import QuizBuildResponse, QuizQuestion

logger = logging.getLogger(__name__)

_MIN_MARKDOWN_CHARS = 900

QUIZ_SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the web for specific concepts, examples, or facts using Exa and Wikipedia. "
                "Use when you need factual context to write accurate quiz questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query for a specific concept or example",
                    },
                    "num_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                        "description": "Number of results to return",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_wikipedia",
            "description": (
                "Search Wikipedia for factual information, definitions, and explanations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Wikipedia search query",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def _quiz_search_executor(tool_name: str, args: dict[str, Any]) -> str:
    """Execute a search tool call for quiz generation."""
    from backend.app.services.web_search import search_web_and_wikipedia, search_wikipedia as search_wiki

    try:
        if tool_name == "search_web":
            query = str(args.get("query") or "").strip()
            num = int(args.get("num_results") or 3)
            if not query:
                return json.dumps({"error": "empty query"})
            result = search_web_and_wikipedia(query=query, num_results=min(num, 5))
            return json.dumps(result, ensure_ascii=True)
        if tool_name == "search_wikipedia":
            query = str(args.get("query") or "").strip()
            if not query:
                return json.dumps({"error": "empty query"})
            result = search_wiki(query=query, num_results=3)
            return json.dumps(result, ensure_ascii=True)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"error": f"unknown tool: {tool_name}"})


def _normalize_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokens(text: str) -> set[str]:
    stop = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "lesson", "quiz"}
    return {
        _normalize_token(t)
        for t in re.findall(r"[a-z0-9]+", text.lower())
        if len(t) > 2 and t not in stop
    }


def format_source_context(context: dict[str, str] | None) -> str | None:
    if not context:
        return None
    text = str(context.get("text") or "").strip()
    if not text:
        return None
    doc = str(context.get("doc") or "source")
    chunk_id = str(context.get("chunk_id") or "")
    prefix = f"{doc}"
    if chunk_id:
        prefix = f"{prefix}#{chunk_id}"
    return f"{prefix}: {text[:600]}"


def best_source_context(question: str, contexts: list[dict[str, str]]) -> str | None:
    if not contexts:
        return None
    q_tokens = tokens(question)
    scored = []
    for idx, context in enumerate(contexts):
        overlap = len(q_tokens.intersection(tokens(context.get("text", "")))) if q_tokens else 0
        scored.append((overlap, -idx, context))
    scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
    return format_source_context(scored[0][2])


def markdown_source_context(markdown: str | None) -> str | None:
    text = re.sub(r"\s+", " ", str(markdown or "")).strip()
    if not text:
        return None
    return f"Checkpoint markdown: {text[:600]}"


def validate_quiz_question(candidate: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return QuizQuestion(**candidate).model_dump()
    except ValueError as exc:
        logger.warning("Dropping invalid lesson quiz question: %s", exc)
        return None


def normalize_quiz(
    raw_quiz: list[Any],
    topic: str,
    planned: dict[str, str],
    contexts: list[dict[str, str]] | None = None,
    markdown: str | None = None,
    max_questions: int = 5,
) -> list[dict[str, Any]]:
    topic_id = re.sub(r'[^a-z0-9]+', '_', topic.lower()).strip('_')
    questions = []
    context_items = contexts or []
    for idx, item in enumerate(raw_quiz[:max(1, int(max_questions))]):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        options = item.get("options") if isinstance(item.get("options"), list) else []
        options = [str(o).strip() for o in options[:4] if str(o).strip()]
        expected_answer = str(item.get("expected_answer") or "").strip()
        correct_idx = item.get("correct_option_index")
        try:
            correct_idx = int(correct_idx)
        except (TypeError, ValueError):
            correct_idx = None

        if len(options) < 2:
            options = []
            correct_idx = None
        elif correct_idx is None or not 0 <= correct_idx < len(options):
            expected_norm = expected_answer.casefold()
            inferred_idx = next(
                (option_idx for option_idx, option in enumerate(options) if option.casefold() == expected_norm),
                None,
            )
            if inferred_idx is None:
                options = []
                correct_idx = None
            else:
                correct_idx = inferred_idx

        if options and correct_idx is not None and not expected_answer:
            expected_answer = options[correct_idx]
        if not expected_answer:
            expected_answer = planned["goal"]

        source_context = (
            str(item.get("source_context") or "").strip()
            or markdown_source_context(markdown)
            or best_source_context(question, context_items)
        )
        candidate = {
            "question_id": f"{topic_id}_{idx + 1}",
            "topic_id": topic_id,
            "topic_title": topic,
            "question": question,
            "question_mode": "mcq" if options else "open",
            "options": options,
            "correct_option_index": correct_idx if options else None,
            "expected_answer": expected_answer,
            "explanation": str(item.get("explanation") or ""),
            "difficulty": planned["difficulty"],
            "source_context": source_context,
        }
        valid = validate_quiz_question(candidate)
        if valid is not None:
            questions.append(valid)
    if questions:
        return questions
    fallback = {
        "question_id": f"{topic_id}_1",
        "topic_id": topic_id,
        "topic_title": topic,
        "question": f"What is the main goal of {planned['title']}?",
        "question_mode": "open",
        "options": [],
        "correct_option_index": None,
        "expected_answer": planned["goal"],
        "explanation": f"This part focuses on: {planned['goal']}",
        "difficulty": planned["difficulty"],
        "source_context": markdown_source_context(markdown) or best_source_context(planned["goal"], context_items),
    }
    valid_fallback = validate_quiz_question(fallback)
    return [valid_fallback or fallback]


def checkpoint_question_mode(quiz: list[dict[str, Any]]) -> str:
    modes = {
        str(question.get("question_mode") or "").strip().lower()
        for question in quiz
        if isinstance(question, dict)
    }
    modes = {mode for mode in modes if mode in {"mcq", "open"}}
    if modes == {"mcq"}:
        return "mcq"
    if modes == {"open"}:
        return "open"
    return "mixed"


def quiz_question_quality_reasons(question: dict[str, Any], markdown: str) -> list[str]:
    reasons: list[str] = []
    if "source_context" in question and not str(question.get("source_context") or "").strip():
        reasons.append("empty_source_context")

    expected_tokens = tokens(str(question.get("expected_answer") or ""))
    if not expected_tokens:
        return reasons

    support_text = " ".join([
        markdown,
        str(question.get("source_context") or ""),
    ])
    support_tokens = tokens(support_text)
    min_overlap = 1 if len(expected_tokens) <= 3 else 2
    overlap = len(expected_tokens.intersection(support_tokens))

    options = question.get("options") if isinstance(question.get("options"), list) else []
    correct_idx = question.get("correct_option_index")
    try:
        correct_idx = int(correct_idx)
    except (TypeError, ValueError):
        correct_idx = None
    correct_option = ""
    if correct_idx is not None and 0 <= correct_idx < len(options):
        correct_option = str(options[correct_idx] or "")
    option_tokens = tokens(correct_option)
    expected_matches_correct_option = bool(expected_tokens) and expected_tokens == option_tokens
    option_supported = bool(option_tokens) and bool(option_tokens.intersection(support_tokens))

    if overlap < min_overlap and not (expected_matches_correct_option and option_supported):
        reasons.append("answer_not_grounded")
    return reasons


def quiz_question_quality_detail(question: dict[str, Any], markdown: str, idx: int) -> list[str]:
    details: list[str] = []
    for reason in quiz_question_quality_reasons(question, markdown):
        qid = str(question.get("question_id") or f"question_{idx + 1}").strip()
        q_text = str(question.get("question") or "").strip()
        expected = str(question.get("expected_answer") or "").strip()
        label = qid
        if q_text:
            label = f"{label}: {q_text[:80]}"
        if expected:
            label = f"{label} -> {expected[:80]}"
        details.append(f"{reason}[{label}]")
    return details


def part_quality_reasons(part: dict[str, Any]) -> list[str]:
    markdown = str(part.get("markdown") or "").strip()
    quiz = part.get("quiz") if isinstance(part.get("quiz"), list) else []
    reasons: list[str] = []
    if bool(part.get("fallback_used")):
        reasons.append("fallback_used=true")
    if len(markdown) < _MIN_MARKDOWN_CHARS:
        reasons.append(f"markdown_chars={len(markdown)}")
    if not quiz:
        reasons.append("quiz_empty")
        return sorted(set(reasons))

    seen_questions: set[str] = set()
    for question in quiz:
        if not isinstance(question, dict):
            reasons.append("quiz_item_not_object")
            continue
        q_text = str(question.get("question") or "").strip().casefold()
        expected = str(question.get("expected_answer") or "").strip()
        if not q_text:
            reasons.append("missing_question")
        if not expected:
            reasons.append("missing_expected_answer")
        if q_text and q_text in seen_questions:
            reasons.append("duplicate_question")
        seen_questions.add(q_text)
        reasons.extend(quiz_question_quality_reasons(question, markdown))
    return sorted(set(reasons))


def find_part_quality_issues(parts: list[dict[str, Any]]) -> list[int]:
    weak: list[int] = []
    for idx, part in enumerate(parts):
        if part_quality_reasons(part):
            weak.append(idx)
    return sorted(set(weak))


def part_quality_issue_summary(parts: list[dict[str, Any]], indexes: list[int]) -> str:
    details = []
    for idx in indexes:
        if idx < 0 or idx >= len(parts):
            continue
        reasons = part_quality_reasons(parts[idx])
        question_details: list[str] = []
        markdown = str(parts[idx].get("markdown") or "").strip()
        quiz = parts[idx].get("quiz") if isinstance(parts[idx].get("quiz"), list) else []
        for question_idx, question in enumerate(quiz):
            if isinstance(question, dict):
                question_details.extend(quiz_question_quality_detail(question, markdown, question_idx))
        reason_text = ", ".join(sorted(set(reasons))) or "unknown"
        if question_details:
            reason_text = f"{reason_text}; questions: {'; '.join(question_details)}"
        details.append(f"{idx + 1} ({reason_text})")
    return "; ".join(details)


def should_regenerate_quiz_only(part: dict[str, Any]) -> bool:
    reasons = set(part_quality_reasons(part))
    if not reasons:
        return False
    markdown_reasons = {reason for reason in reasons if reason.startswith("markdown_chars") or reason == "fallback_used=true"}
    return not markdown_reasons


async def generate_quiz(
    *,
    topic: str,
    num_questions: int = 5,
    question_mode: str = "mcq",
    difficulty: str = "medium",
    contexts: list[dict[str, str]] | None = None,
    context_source: str | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> QuizBuildResponse:
    """Generate a standalone quiz on a topic via LLM, with normalization."""
    from backend.app.services.llm_json import call_json_model_with_tools

    topic_id = re.sub(r'[^a-z0-9]+', '_', topic.lower()).strip('_')
    quiz_id = f"quiz_{topic_id}_{uuid.uuid4().hex[:8]}"

    mode_instruction = (
        "Each question must have 4 multiple-choice options with correct_option_index pointing to the right one."
        if question_mode == "mcq"
        else "Each question must be open-ended with no options (empty options list, null correct_option_index)."
    )

    planned = {
        "title": topic,
        "difficulty": difficulty or "medium",
        "goal": f"Demonstrate understanding of {topic}",
    }
    context_items = contexts or []
    context_instruction = ""
    if context_items:
        context_instruction = (
            "Use the supplied local context as the primary source for every question. "
            "Questions and expected answers must be grounded in that context."
        )

    try:
        generated = await call_json_model_with_tools(
            role="quiz_generator",
            payload={
                "topic": topic,
                "num_questions": num_questions,
                "question_mode": question_mode,
                "difficulty": planned["difficulty"],
                "context_source": context_source,
                "context": context_items,
                "required_output_schema": {
                    "quiz": [
                        {
                            "question": "string",
                            "options": ["string", "string", "string", "string"],
                            "correct_option_index": "integer 0-3",
                            "expected_answer": "string",
                            "explanation": "string",
                        }
                    ],
                },
                "task": (
                    f"Generate {num_questions} {planned['difficulty']} quiz questions about {topic}. "
                    f"{mode_instruction} "
                    f"{context_instruction} "
                    "Use search_web or search_wikipedia to verify facts and find accurate information "
                    "when supplied context is missing or insufficient. Every expected_answer must be factually accurate "
                    "and directly answer the question. Vary difficulty across questions. Avoid duplicates."
                ),
            },
            tools=QUIZ_SEARCH_TOOLS,
            tool_executor=_quiz_search_executor,
            max_tool_rounds=2,
            fallback={"quiz": []},
            event_callback=event_callback,
        )
    except Exception as exc:
        logger.warning("quiz_generation LLM call failed topic=%r error=%s", topic, exc)
        generated = {"quiz": []}

    build_indicator: str = "ok"
    raw_quiz = generated.get("quiz") if isinstance(generated, dict) else None
    if not isinstance(raw_quiz, list) or not raw_quiz:
        logger.warning(
            "quiz_generation fell back to default topic=%r generated_keys=%s",
            topic,
            list(generated.keys()) if isinstance(generated, dict) else type(generated).__name__,
        )
        build_indicator = "fallback_used"
        raw_quiz = [
            {
                "question": f"What is the main concept of {topic}?",
                "expected_answer": f"{topic} is a key concept.",
                "explanation": f"This tests basic understanding of {topic}.",
            }
        ]

    questions = normalize_quiz(raw_quiz, topic, planned, contexts=context_items, max_questions=num_questions)

    quiz_questions: list[QuizQuestion] = []
    for q in questions:
        try:
            quiz_questions.append(QuizQuestion(**q))
        except ValueError:
            continue

    if not quiz_questions:
        build_indicator = "fallback_used"
        fallback_q = QuizQuestion(
            question_id=f"{topic_id}_1",
            topic_id=topic_id,
            topic_title=topic,
            question=f"What is the main concept of {topic}?",
            expected_answer=f"{topic} is a key concept.",
            difficulty="medium",
            question_mode="open",
            options=None,
            correct_option_index=None,
        )
        quiz_questions.append(fallback_q)

    return QuizBuildResponse(
        quiz_id=quiz_id,
        topic_filter=topic,
        difficulty_filter=planned["difficulty"],
        questions=quiz_questions,
        build_indicator=build_indicator,
    )
