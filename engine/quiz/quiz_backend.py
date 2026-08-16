# Authored by Noah — https://github.com/noah-e1528170
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import random
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from engine.pipeline.constants import DEFAULT_QA_OUTPUT
from engine.pipeline.models import CurriculumQAOutput, QuestionAnswer, TopicQASet
from engine.quiz.model_setup import MODEL_NAME

CORRECTNESS_THRESHOLD = 0.58
SEMANTIC_WEIGHT = 0.7
LEXICAL_WEIGHT = 0.3
MAX_GENERATED_RATIO = 0.4
MAX_VARIANTS_PER_BASE = 1
TOKEN_TYPO_SIMILARITY = 0.85

_TRAILING_VERBS = (" work", " works", " traverse", " traverses", " operate", " operates",
                    " function", " functions", " behave", " behaves",
                    " used", " applied", " implemented", " constructed",
                    " important", " significant", " valuable", " useful",
                    " matters", " happen", " occur", " be")

_STOP_WORDS = frozenset({"the", "a", "an", "is", "are", "was", "were", "be", "been",
                          "being", "have", "has", "had", "do", "does", "did", "will",
                          "would", "could", "should", "may", "might", "can", "shall",
                          "to", "of", "in", "for", "on", "with", "at", "by", "from",
                          "as", "into", "about", "between", "through", "during", "before",
                          "after", "above", "below", "and", "but", "or", "nor", "not",
                          "so", "yet", "both", "either", "neither", "each", "every",
                          "this", "that", "these", "those", "it", "its"})

_LEADING_DETERMINERS = ("these ", "this ", "those ", "the ")
_LEADING_VERBS = ("is ", "are ", "was ", "were ", "be ")


class QuizBuildRequest(BaseModel):
    user_prompt: str = Field(default="")
    topic: str | None = None
    exclude_topic: str | None = None
    difficulty: str | None = None
    num_questions: int = Field(default=5, ge=1, le=50)
    shuffle: bool = False
    question_mode: Literal["open", "mcq"] = "open"
    allow_generated_fallback: bool = True


class QuizQuestion(BaseModel):
    question_id: str
    topic_id: str
    topic_title: str
    question: str
    expected_answer: str
    difficulty: str
    source_context: str | None = None
    question_mode: Literal["open", "mcq"] = "open"
    options: list[str] | None = None
    correct_option_index: int | None = None


class QuizBuildResponse(BaseModel):
    quiz_id: str
    topic_filter: str | None = None
    exclude_topic_filter: str | None = None
    difficulty_filter: str | None = None
    build_indicator: Literal["ok", "fallback_used", "failed"] = "ok"
    failure_reason: Literal[
        "none",
        "exclude_eliminated_all",
        "insufficient_after_filters",
        "generation_cap_reached",
        "no_questions_available",
    ] = "none"
    questions: list[QuizQuestion]
    warnings: list[str] = Field(default_factory=list)


class QuizSubmissionAnswer(BaseModel):
    question_id: str
    answer: str


class QuizGradeRequest(BaseModel):
    quiz_id: str
    answers: list[QuizSubmissionAnswer]


class QuizGradedQuestion(BaseModel):
    question_id: str
    is_correct: bool
    expected_answer: str
    submitted_answer: str
    score: float


class QuizGradeResponse(BaseModel):
    quiz_id: str
    total_questions: int
    answered_questions: int
    correct_answers: int
    score_percent: float
    results: list[QuizGradedQuestion]


class QuestionCandidate(BaseModel):
    topic_id: str
    topic_title: str
    qa: QuestionAnswer


_EMBED_CACHE: dict[str, list[float]] = {}
_embed_text_fn = None
_embed_text_dim = 96


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quiz backend tools for terminal testing.")
    parser.add_argument(
        "--qa-output",
        default=str(DEFAULT_QA_OUTPUT),
        help="Path to curriculum+QA JSON output file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-quiz", help="Build a quiz from QA output.")
    build.add_argument("--request-file", required=True, help="Path to JSON request payload.")

    grade = sub.add_parser("grade-quiz", help="Grade a quiz submission.")
    grade.add_argument("--request-file", required=True, help="Path to JSON grade payload.")
    grade.add_argument(
        "--quiz-file",
        required=True,
        help="Path to JSON file previously returned by build-quiz.",
    )
    return parser


def run() -> None:
    args = build_parser().parse_args()
    qa_output = _load_qa_output(Path(args.qa_output).resolve())

    if args.command == "build-quiz":
        request_payload = _load_json(Path(args.request_file).resolve())
        request = QuizBuildRequest.model_validate(request_payload)
        response = build_quiz(qa_output=qa_output, request=request)
        print(json.dumps(response.model_dump(), ensure_ascii=True, indent=2))
        return

    if args.command == "grade-quiz":
        request_payload = _load_json(Path(args.request_file).resolve())
        quiz_payload = _load_json(Path(args.quiz_file).resolve())
        request = QuizGradeRequest.model_validate(request_payload)
        quiz = QuizBuildResponse.model_validate(quiz_payload)
        response = grade_quiz(quiz=quiz, request=request)
        print(json.dumps(response.model_dump(), ensure_ascii=True, indent=2))
        return

    raise RuntimeError(f"Unsupported command: {args.command}")


def build_quiz(*, qa_output: CurriculumQAOutput, request: QuizBuildRequest) -> QuizBuildResponse:
    pools = qa_output.qa_sets
    warnings: list[str] = []
    generated_count = 0
    build_indicator: Literal["ok", "fallback_used", "failed"] = "ok"
    failure_reason: Literal[
        "none",
        "exclude_eliminated_all",
        "insufficient_after_filters",
        "generation_cap_reached",
        "no_questions_available",
    ] = "none"

    topic_filter = (request.topic or "").strip()
    if topic_filter:
        pools = _filter_topics(pools, topic_filter)
        if not pools:
            warnings.append(f"No topic matched '{topic_filter}'. Falling back to all topics.")
            pools = qa_output.qa_sets
    else:
        topic_filter = None

    exclude_topic_filter = (request.exclude_topic or "").strip()
    if exclude_topic_filter:
        excluded = _exclude_topics(pools, exclude_topic_filter)
        if excluded:
            pools = excluded
        else:
            warnings.append(f"Exclude filter '{exclude_topic_filter}' removed all topics.")
            build_indicator = "failed"
            failure_reason = "exclude_eliminated_all"
            quiz_id = _build_quiz_id(
                topic_filter=topic_filter,
                difficulty_filter=request.difficulty,
                num_requested=request.num_questions,
                shuffle=request.shuffle,
                question_mode=request.question_mode,
                picked=[],
                exclude_topic_filter=exclude_topic_filter,
                generated_count=0,
            )
            return QuizBuildResponse(
                quiz_id=quiz_id,
                topic_filter=topic_filter,
                exclude_topic_filter=exclude_topic_filter,
                difficulty_filter=_normalize_difficulty(request.difficulty),
                build_indicator=build_indicator,
                failure_reason=failure_reason,
                questions=[],
                warnings=warnings,
            )
    else:
        exclude_topic_filter = None

    all_questions = _flatten_questions(pools)
    fallback_source = all_questions[:]
    difficulty_filter = _normalize_difficulty(request.difficulty)
    if difficulty_filter:
        filtered = [q for q in all_questions if _normalize_difficulty(q.qa.difficulty) == difficulty_filter]
        if filtered:
            all_questions = filtered
        else:
            warnings.append(
                f"No questions found for difficulty '{difficulty_filter}'. Falling back to mixed difficulty."
            )
    else:
        difficulty_filter = None

    if not all_questions:
        warnings.append("No questions available after applying filters.")
        build_indicator = "failed"
        failure_reason = "no_questions_available"
        quiz_id = _build_quiz_id(
            topic_filter=topic_filter,
            difficulty_filter=difficulty_filter,
            num_requested=request.num_questions,
            shuffle=request.shuffle,
            question_mode=request.question_mode,
            picked=[],
            exclude_topic_filter=exclude_topic_filter,
            generated_count=0,
        )
        return QuizBuildResponse(
            quiz_id=quiz_id,
            topic_filter=topic_filter,
            exclude_topic_filter=exclude_topic_filter,
            difficulty_filter=difficulty_filter,
            build_indicator=build_indicator,
            failure_reason=failure_reason,
            questions=[],
            warnings=warnings,
        )

    picked = all_questions[:]
    if request.shuffle:
        random.shuffle(picked)

    if len(picked) < request.num_questions:
        missing = request.num_questions - len(picked)
        if request.allow_generated_fallback and fallback_source:
            max_generated = max(1, int(round(request.num_questions * MAX_GENERATED_RATIO)))
            generate_count = min(missing, max_generated)
            generated = _generate_fallback_candidates(
                request=request,
                fallback_pool=fallback_source,
                existing=picked,
                count=generate_count,
            )
            if generated:
                picked.extend(generated)
                generated_count = len(generated)
                build_indicator = "fallback_used"
                warnings.append(
                    f"Generated {generated_count} fallback question(s) to satisfy requested count {request.num_questions}."
                )
        if len(picked) < request.num_questions:
            build_indicator = "failed"
            if generated_count > 0:
                failure_reason = "generation_cap_reached"
            else:
                failure_reason = "insufficient_after_filters"
            warnings.append(
                f"Requested {request.num_questions} questions but only {len(picked)} available after filtering."
            )
    picked = picked[: request.num_questions]
    candidate_answers = [item.qa.answer for item in all_questions]

    quiz_id = _build_quiz_id(
        topic_filter=topic_filter,
        difficulty_filter=difficulty_filter,
        num_requested=request.num_questions,
        shuffle=request.shuffle,
        question_mode=request.question_mode,
        picked=picked,
        exclude_topic_filter=exclude_topic_filter,
        generated_count=generated_count,
    )

    questions: list[QuizQuestion] = []
    for idx, item in enumerate(picked, start=1):
        options = None
        correct_option_index = None
        if request.question_mode == "mcq":
            options, correct_option_index = _build_mcq_options(
                correct_answer=item.qa.answer,
                answer_pool=candidate_answers,
                seed=f"{quiz_id}:{idx}",
            )
        questions.append(
            QuizQuestion(
                question_id=f"{quiz_id}_q{idx:02d}",
                topic_id=item.topic_id,
                topic_title=item.topic_title,
                question=item.qa.question,
                expected_answer=item.qa.answer,
                difficulty=item.qa.difficulty,
                source_context=item.qa.source_context,
                question_mode=request.question_mode,
                options=options,
                correct_option_index=correct_option_index,
            )
        )

    return QuizBuildResponse(
        quiz_id=quiz_id,
        topic_filter=topic_filter,
        exclude_topic_filter=exclude_topic_filter,
        difficulty_filter=difficulty_filter,
        build_indicator=build_indicator,
        failure_reason=failure_reason,
        questions=questions,
        warnings=warnings,
    )


def grade_quiz(*, quiz: QuizBuildResponse, request: QuizGradeRequest) -> QuizGradeResponse:
    if request.quiz_id != quiz.quiz_id:
        raise RuntimeError(
            f"Quiz id mismatch: request has '{request.quiz_id}', but quiz file has '{quiz.quiz_id}'."
        )

    by_id = {q.question_id: q for q in quiz.questions}
    results: list[QuizGradedQuestion] = []
    for submitted in request.answers:
        q = by_id.get(submitted.question_id)
        if q is None:
            continue
        score = _grade_question_answer(q=q, submitted=submitted.answer)
        results.append(
            QuizGradedQuestion(
                question_id=q.question_id,
                is_correct=score >= CORRECTNESS_THRESHOLD,
                expected_answer=q.expected_answer,
                submitted_answer=submitted.answer,
                score=round(score, 3),
            )
        )

    answered = len(results)
    total = len(quiz.questions)
    correct = sum(1 for r in results if r.is_correct)
    return QuizGradeResponse(
        quiz_id=quiz.quiz_id,
        total_questions=total,
        answered_questions=answered,
        correct_answers=correct,
        score_percent=round((correct / total) * 100.0, 2) if total else 0.0,
        results=results,
    )


def _filter_topics(pools: list[TopicQASet], topic_filter: str) -> list[TopicQASet]:
    needle = topic_filter.lower()
    out: list[TopicQASet] = []
    for topic in pools:
        if needle in topic.topic_title.lower() or needle in topic.topic_id.lower():
            out.append(topic)
    return out


def _exclude_topics(pools: list[TopicQASet], exclude_filter: str) -> list[TopicQASet]:
    needle = exclude_filter.lower()
    out: list[TopicQASet] = []
    for topic in pools:
        blob = f"{topic.topic_title} {topic.topic_id}".lower()
        if needle in blob:
            continue
        out.append(topic)
    return out


def _flatten_questions(pools: list[TopicQASet]) -> list[QuestionCandidate]:
    out: list[QuestionCandidate] = []
    for topic in pools:
        for qa in topic.questions:
            out.append(QuestionCandidate(topic_id=topic.topic_id, topic_title=topic.topic_title, qa=qa))
    return out


def _normalize_difficulty(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip().lower()
    if clean in {"easy", "medium", "hard"}:
        return clean
    return None


def _build_quiz_id(
    *,
    topic_filter: str | None,
    difficulty_filter: str | None,
    num_requested: int,
    shuffle: bool,
    question_mode: str,
    picked: list[QuestionCandidate],
    exclude_topic_filter: str | None = None,
    generated_count: int = 0,
) -> str:
    question_fingerprint = "|".join(
        f"{item.topic_id}::{item.qa.question}::{item.qa.answer}::{item.qa.difficulty}" for item in picked
    )
    seed = (
        f"topic={topic_filter or ''}|difficulty={difficulty_filter or ''}|"
        f"exclude={exclude_topic_filter or ''}|num={num_requested}|shuffle={shuffle}|"
        f"mode={question_mode}|generated={generated_count}|{question_fingerprint}"
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"quiz_{digest}"


def _generate_fallback_candidates(
    *,
    request: QuizBuildRequest,
    fallback_pool: list[QuestionCandidate],
    existing: list[QuestionCandidate],
    count: int,
) -> list[QuestionCandidate]:
    if count <= 0:
        return []
    difficulty = _normalize_difficulty(request.difficulty)
    preferred = fallback_pool
    if difficulty:
        difficulty_first = [c for c in fallback_pool if _normalize_difficulty(c.qa.difficulty) == difficulty]
        if difficulty_first:
            preferred = difficulty_first
    generated: list[QuestionCandidate] = []
    # Allow reusing base items to synthesize variants when strict filtering yields too few.
    if not preferred:
        return []
    per_base_used: dict[str, int] = {}
    idx = 0
    while len(generated) < count and idx < max(1, len(preferred) * (MAX_VARIANTS_PER_BASE + 1) * 3):
        base = preferred[idx % len(preferred)]
        idx += 1
        base_key = f"{base.topic_id}::{base.qa.question}"
        used = per_base_used.get(base_key, 0)
        if used >= MAX_VARIANTS_PER_BASE:
            continue
        per_base_used[base_key] = used + 1
        generated.append(_make_generated_candidate(base=base, variant_index=(len(generated) + 1)))
    return generated


def _make_generated_candidate(*, base: QuestionCandidate, variant_index: int) -> QuestionCandidate:
    generated_question = _rephrase_question(base.qa.question, variant_index)
    generated_qa = QuestionAnswer(
        question=generated_question,
        answer=base.qa.answer,
        difficulty=base.qa.difficulty,
        source_context=base.qa.source_context,
    )
    return QuestionCandidate(
        topic_id=base.topic_id,
        topic_title=base.topic_title,
        qa=generated_qa,
    )


def _strip_trailing_verb(s: str) -> str:
    for v in _TRAILING_VERBS:
        if s.endswith(v):
            return s[: -len(v)].strip()
    return s


def _strip_leading_det_verb(s: str) -> str:
    for prefix in _LEADING_DETERMINERS:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    for v in _LEADING_VERBS:
        if s.startswith(v):
            s = s[len(v):]
            break
    return s.strip()


_PUNCT_RE = re.compile(r"[?.!]+$")


def _rephrase_question(question: str, variant_index: int) -> str:
    text = question.strip()
    if not text:
        return f"Generated question #{variant_index}: explain the key concept."
    lowered = _PUNCT_RE.sub("", text.lower())

    if lowered.startswith("what is "):
        body = _strip_trailing_verb(lowered[8:].strip())
        return f"In your own words, what is {body}?"
    if lowered.startswith("what are "):
        body = _strip_trailing_verb(lowered[9:].strip())
        return f"List the key characteristics of {body}."
    if lowered.startswith("explain why "):
        body = _strip_trailing_verb(lowered[12:].strip())
        return f"Give a concise explanation of why {body}."
    if lowered.startswith("explain how "):
        body = _strip_trailing_verb(lowered[12:].strip())
        return f"Describe the process of {body}."
    if lowered.startswith("how does "):
        body = _strip_trailing_verb(lowered[9:].strip())
        return f"Describe the mechanism by which {body}."
    if lowered.startswith("how do "):
        body = _strip_trailing_verb(lowered[7:].strip())
        return f"Explain the steps involved in {body}."
    if lowered.startswith("describe "):
        body = _strip_trailing_verb(lowered[9:].strip())
        return f"Summarize clearly: {body}."
    if lowered.startswith("which of "):
        body = _strip_leading_det_verb(_strip_trailing_verb(lowered[9:].strip()))
        return f"Identify the correct statement about {body}."
    if lowered.startswith("when should you "):
        body = _strip_trailing_verb(lowered[16:].strip())
        if body.startswith("use "):
            body = body[4:].strip()
        return f"In what situations should you use {body}?"
    if lowered.startswith("when to "):
        body = _strip_trailing_verb(lowered[8:].strip())
        return f"In what situations should you use {body}?"
    if lowered.startswith("where is "):
        body = _strip_trailing_verb(lowered[9:].strip())
        return f"Name the contexts where {body} is applicable."
    if lowered.startswith("where are "):
        body = _strip_trailing_verb(lowered[10:].strip())
        return f"Name the contexts where {body} are applicable."
    if lowered.startswith("why is "):
        body = _strip_trailing_verb(lowered[7:].strip())
        return f"What is the significance of {body}?"
    if lowered.startswith("why are "):
        body = _strip_trailing_verb(lowered[8:].strip())
        return f"What is the significance of {body}?"
    return f"Regarding the concept: {text}"


def _tokenize(text: str) -> set[str]:
    parts = re.findall(r"[a-z0-9]+", text.lower())
    return set(parts)


def _answer_score(*, expected: str, submitted: str) -> float:
    lexical = _lexical_score(expected=expected, submitted=submitted)
    semantic = _semantic_similarity(expected=expected, submitted=submitted)
    if semantic is None:
        return lexical
    return (SEMANTIC_WEIGHT * semantic) + (LEXICAL_WEIGHT * lexical)


def _grade_question_answer(*, q: QuizQuestion, submitted: str) -> float:
    if q.question_mode == "mcq" and q.options and q.correct_option_index is not None:
        selected = _parse_mcq_selection(submitted=submitted, options=q.options)
        if selected is None:
            return 0.0
        return 1.0 if selected == q.correct_option_index else 0.0
    return _answer_score(expected=q.expected_answer, submitted=submitted)


def _parse_mcq_selection(*, submitted: str, options: list[str]) -> int | None:
    text = submitted.strip()
    if not text:
        return None
    upper = text.upper()
    if len(upper) == 1 and "A" <= upper <= "Z":
        idx = ord(upper) - ord("A")
        if 0 <= idx < len(options):
            return idx
    for idx, option in enumerate(options):
        if text.strip().lower() == option.strip().lower():
            return idx
    # Allow minor typos when the learner types the option text instead of letter.
    norm_text = _normalize_text(text)
    if norm_text:
        best_idx = None
        best_ratio = 0.0
        for idx, option in enumerate(options):
            ratio = difflib.SequenceMatcher(None, norm_text, _normalize_text(option)).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = idx
        if best_idx is not None and best_ratio >= TOKEN_TYPO_SIMILARITY:
            return best_idx
    return None


def _build_mcq_options(*, correct_answer: str, answer_pool: list[str], seed: str) -> tuple[list[str], int]:
    unique_pool = []
    seen = set()
    correct_norm = correct_answer.strip().lower()
    for ans in answer_pool:
        norm = ans.strip().lower()
        if not norm or norm == correct_norm or norm in seen:
            continue
        seen.add(norm)
        unique_pool.append(ans.strip())

    scored = []
    correct_tokens = _tokenize(correct_answer) - _STOP_WORDS
    for ans in unique_pool:
        ans_tokens = _tokenize(ans) - _STOP_WORDS
        overlap = len(correct_tokens & ans_tokens)
        len_diff = abs(len(ans) - len(correct_answer))
        score = overlap * 2 - len_diff * 0.1
        scored.append((score, ans))
    scored.sort(key=lambda x: x[0], reverse=True)

    distractors = [ans for _, ans in scored[:3]]

    fallbacks = [
        "None of the above",
        "All of the above",
        "Not enough information to answer",
        "The question is unclear",
    ]
    fallback_idx = 0
    while len(distractors) < 3 and fallback_idx < len(fallbacks) * 2:
        fallback = fallbacks[fallback_idx % len(fallbacks)]
        fallback_idx += 1
        if fallback.lower() != correct_norm and fallback not in distractors:
            distractors.append(fallback)

    rnd = random.Random(seed)
    options = [correct_answer.strip()] + distractors
    rnd.shuffle(options)
    correct_idx = options.index(correct_answer.strip())
    return options, correct_idx


def _lexical_score(*, expected: str, submitted: str) -> float:
    expected_tokens = _tokenize(expected)
    submitted_tokens = _tokenize(submitted)
    if not expected_tokens:
        return 0.0
    if not submitted_tokens:
        return 0.0
    overlap = _fuzzy_token_overlap(expected_tokens=expected_tokens, submitted_tokens=submitted_tokens)
    precision = len(overlap) / len(submitted_tokens)
    recall = len(overlap) / len(expected_tokens)
    if precision + recall <= 0.0:
        return 0.0
    f1 = (2.0 * precision * recall) / (precision + recall)
    # Favor concise but correct answers by weighting F1, with a smaller recall component.
    return (0.7 * f1) + (0.3 * recall)


def _fuzzy_token_overlap(*, expected_tokens: set[str], submitted_tokens: set[str]) -> set[str]:
    overlap = expected_tokens.intersection(submitted_tokens)
    unmatched_expected = [tok for tok in expected_tokens if tok not in overlap]
    unmatched_submitted = [tok for tok in submitted_tokens if tok not in overlap]
    if not unmatched_expected or not unmatched_submitted:
        return overlap
    for expected_tok in unmatched_expected:
        for submitted_tok in unmatched_submitted:
            if _is_typo_match(expected_tok, submitted_tok):
                overlap.add(expected_tok)
                break
    return overlap


def _is_typo_match(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if min(len(a), len(b)) < 4:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= TOKEN_TYPO_SIMILARITY


def _normalize_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _semantic_similarity(*, expected: str, submitted: str) -> float | None:
    expected_vec = _embed_text(expected)
    submitted_vec = _embed_text(submitted)
    if expected_vec is None or submitted_vec is None:
        return None
    return _cosine_similarity(expected_vec, submitted_vec)


def _embed_text(text: str) -> list[float] | None:
    global _embed_text_fn, _embed_text_dim
    if text in _EMBED_CACHE:
        return _EMBED_CACHE[text]
    if _embed_text_fn is None:
        from engine.shared.embedding_loader import build_embed_fn

        _embed_text_fn, _embed_text_dim, _fallback = build_embed_fn(MODEL_NAME)
    vector = _embed_text_fn(text)
    _EMBED_CACHE[text] = vector
    return vector


def preload_embedding_model() -> bool:
    probe = _embed_text("model warmup")
    return probe is not None


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
    # normalize -1..1 into 0..1 for grading blend
    return max(0.0, min(1.0, (dot / denom + 1.0) / 2.0))


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"File not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_qa_output(path: Path) -> CurriculumQAOutput:
    payload = _load_json(path)
    return CurriculumQAOutput.model_validate(payload)


if __name__ == "__main__":
    run()
