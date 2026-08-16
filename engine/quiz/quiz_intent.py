from __future__ import annotations

import re

from pydantic import BaseModel

from engine.quiz.quiz_backend import QuizBuildRequest


class ParsedIntent(BaseModel):
    topic: str | None = None
    exclude_topic: str | None = None
    difficulty: str = "medium"
    num_questions: int = 5
    question_mode: str = "open"


def parse_quiz_intent(prompt: str) -> ParsedIntent:
    text = prompt.strip()
    lower = text.lower()

    difficulty = "medium"
    for level in ("easy", "medium", "hard"):
        if re.search(rf"\b{level}\b", lower):
            difficulty = level
            break

    num_questions = _extract_num_questions(lower)

    topic = _extract_topic(text)
    exclude_topic = _extract_excluded_topic(text)
    if topic and exclude_topic and topic.strip().lower() == exclude_topic.strip().lower():
        topic = None
    if topic and exclude_topic and exclude_topic.strip().lower() in topic.strip().lower():
        topic = None
    if topic and topic.strip().lower().startswith("not "):
        topic = None
    question_mode = _extract_question_mode(lower)
    return ParsedIntent(
        topic=topic,
        exclude_topic=exclude_topic,
        difficulty=difficulty,
        num_questions=num_questions,
        question_mode=question_mode,
    )


def to_build_request(prompt: str) -> QuizBuildRequest:
    parsed = parse_quiz_intent(prompt)
    return QuizBuildRequest(
        user_prompt=prompt,
        topic=parsed.topic,
        exclude_topic=parsed.exclude_topic,
        difficulty=parsed.difficulty,
        num_questions=parsed.num_questions,
        shuffle=False,
        question_mode=parsed.question_mode,
    )


def _extract_question_mode(lower_prompt: str) -> str:
    if any(token in lower_prompt for token in {"multiple choice", "mcq", "choices"}):
        return "mcq"
    if any(token in lower_prompt for token in {"open ended", "open-ended", "free response", "short answer"}):
        return "open"
    return "open"


def _extract_topic(prompt: str) -> str | None:
    # Handle common natural forms:
    # "quiz on avl trees", "questions about dijkstra", "test me on hashing"
    patterns = [
        r"\b(?:quiz|questions?|test)\s+(?:on|about)\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt.strip(), flags=re.IGNORECASE)
        if match:
            topic = match.group(1).strip(" .!?")
            if topic:
                return topic
    inferred = _infer_topic_phrase(prompt)
    if inferred:
        return inferred
    return None


def _extract_num_questions(lower_prompt: str) -> int:
    # Explicit numeric count near question-related keywords takes priority.
    match_near_q = re.search(
        r"\b(?:give|want|need|show|create|make|generate|how\s+about)\s+(?:me\s+)?(\d{1,2})\s+(?:question|q)\b",
        lower_prompt,
    )
    if match_near_q:
        return max(1, min(50, int(match_near_q.group(1))))
    # Fallback: first 1-2 digit number anywhere.
    match_num = re.search(r"\b(\d{1,2})\b", lower_prompt)
    if match_num:
        return max(1, min(50, int(match_num.group(1))))

    # Support common number words.
    word_to_num = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    for word, value in word_to_num.items():
        if re.search(rf"\b{word}\b", lower_prompt):
            return value

    # Singular phrasing should map to one question.
    singular_patterns = (
        r"\ba question\b",
        r"\bone question\b",
        r"\bsingle question\b",
        r"\bjust one\b",
        r"\bgive me a\b.*\bquestion\b",
        r"\bcan you give me a\b.*\bquestion\b",
    )
    for pattern in singular_patterns:
        if re.search(pattern, lower_prompt):
            return 1

    return 5


def _extract_excluded_topic(prompt: str) -> str | None:
    patterns = [
        r"\bnot\s+about\s+(.+)$",
        r"\bexcept\s+(.+)$",
        r"\bbut\s+not\s+(.+)$",
        r"\bexclude\s+(.+)$",
        r"\bwithout\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt.strip(), flags=re.IGNORECASE)
        if not match:
            continue
        topic = match.group(1).strip(" .!?")
        topic = re.sub(r"\b(please|thanks?)\b$", "", topic, flags=re.IGNORECASE).strip(" .!?")
        if topic:
            return topic
    return None


def _infer_topic_phrase(prompt: str) -> str | None:
    text = prompt.strip().lower()
    if not text:
        return None
    # Normalize punctuation to spaces for predictable token filtering.
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if t]
    if not tokens:
        return None

    stopwords = {
        "can",
        "could",
        "you",
        "give",
        "me",
        "please",
        "a",
        "an",
        "the",
        "just",
        "on",
        "about",
        "quiz",
        "question",
        "questions",
        "test",
        "ask",
        "hard",
        "medium",
        "easy",
        "mcq",
        "multiple",
        "choice",
        "choices",
        "open",
        "ended",
        "free",
        "response",
        "short",
        "answer",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
    }
    kept = [t for t in tokens if t not in stopwords and not re.fullmatch(r"\d{1,2}", t)]
    if not kept:
        return None
    return " ".join(kept)
