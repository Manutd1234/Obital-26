from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
import re

from engine.pipeline.config import load_settings
from engine.quiz.account_system import AccountStore
from engine.quiz.quiz_backend import (
    QuizBuildResponse,
    QuizBuildRequest,
    build_quiz,
    preload_embedding_model,
)
from engine.quiz.quiz_intent import to_build_request
from engine.quiz.quiz_tui import QuizIntentRouter, run_quiz_session
from engine.shared.account_paths import (
    account_output_dir,
    account_qa_output_path,
    normalize_account_id,
)

TOP_LEVEL_TOOL_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "route_top_level_intent",
            "description": "Route top-level chatbot intent for lesson/dashboard navigation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list_lessons",
                            "lesson_status",
                            "open_lesson",
                            "resume_lesson",
                            "restart_lesson",
                            "start_lesson",
                            "continue_lesson",
                            "dashboard",
                            "build_quiz",
                            "ask",
                            "exit",
                        ],
                    },
                    "selector": {"type": ["string", "null"]},
                    "topic": {"type": ["string", "null"]},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    }
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chatbot-style CLI for quiz generation and grading.")
    parser.add_argument(
        "--qa-output",
        default=None,
        help="Path to curriculum+QA JSON output file (defaults to account-scoped output).",
    )
    parser.add_argument(
        "--account-id",
        default=None,
        help="Optional account id override for account-scoped documents/output paths.",
    )
    return parser


def run() -> None:
    args = build_parser().parse_args()
    intent_router = _build_intent_router()
    account_store = AccountStore(Path("engine/accounts/accounts.json"))
    username, profile = _auth_flow(account_store)
    print("Loading quiz engine...")
    _ = preload_embedding_model()
    print("Quiz engine ready.\n")
    base_dir = Path(".").resolve()
    effective_account_id = normalize_account_id(args.account_id or username)
    account_out_dir = account_output_dir(base_dir=base_dir, account_id=effective_account_id)
    qa_output_path = (
        Path(args.qa_output).expanduser().resolve()
        if args.qa_output
        else account_qa_output_path(base_dir=base_dir, account_id=effective_account_id)
    )
    qa_output = _load_qa_output(qa_output_path)

    print("Obital Quiz Chatbot (terminal)")
    print(f"Signed in as: {username} | Account id: {effective_account_id} | Points: {profile.get('total_points', 0)}")
    print("Type a request like: give me 5 medium questions on avl trees")
    print("Type: exit\n")

    while True:
        prompt = input("You: ").strip()
        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit", "/exit", "/quit"}:
            print("Bye.")
            return
        # Precedence: LLM tool-routing first, then deterministic local rules as fallback.
        routed = _route_top_level_action(prompt=prompt, intent_router=intent_router)
        if routed is not None:
            if _handle_routed_top_level_action(
                routed=routed,
                username=username,
                account_store=account_store,
                intent_router=intent_router,
                output_dir=account_out_dir,
                prompt=prompt,
            ):
                continue
        if _is_dashboard_request(prompt):
            print(_format_dashboard_text(account_store=account_store, username=username))
            print()
            continue
        if _handle_lesson_command(
            prompt=prompt,
            username=username,
            account_store=account_store,
            intent_router=intent_router,
            output_dir=account_out_dir,
        ):
            continue
        if prompt.startswith("/"):
            print("Bot: No active quiz right now. Ask for a quiz topic or type exit.\n")
            continue

        if _is_general_question_heuristic(prompt):
            if intent_router is not None:
                _answer_general_question(prompt, intent_router)
            else:
                print("Bot: I am offline and cannot answer general questions. If you wanted to start a quiz, try: 'quiz on <topic>'.\n")
            continue

        request = to_build_request(prompt)
        quiz = build_quiz(qa_output=qa_output, request=request)
        _save_json(account_out_dir / "quiz_built.json", quiz.model_dump())
        print(
            f"Bot: Built quiz '{quiz.quiz_id}' with {len(quiz.questions)} questions "
            f"(status: {quiz.build_indicator})."
        )
        if quiz.warnings:
            for warning in quiz.warnings:
                print(f"Bot warning: {warning}")
        if quiz.build_indicator == "failed" or not quiz.questions:
            print(f"Bot: Quiz build failed ({quiz.failure_reason}).")
            print(f"Bot: {_failure_hint(quiz.failure_reason)}\n")
            continue

        try:
            result = run_quiz_session(
                quiz,
                intent_router=intent_router,
                dashboard_handler=lambda: _format_dashboard_text(account_store=account_store, username=username),
            )
        except RuntimeError as exc:
            if str(exc) == "Quiz session quit by user.":
                _save_router_telemetry(
                    intent_router=intent_router,
                    username=username,
                    quiz_id=quiz.quiz_id,
                    session_status="cancelled",
                    output_dir=account_out_dir,
                )
                print("Bot: Quiz cancelled. You can ask for a new quiz or type exit.\n")
                continue
            raise
        _save_router_telemetry(
            intent_router=intent_router,
            username=username,
            quiz_id=result.quiz_id,
            session_status="submitted",
            output_dir=account_out_dir,
        )
        result_path = account_out_dir / "quiz_result.json"
        _save_json(result_path, result.model_dump())
        print(f"Bot: Result saved to {result_path}\n")
        update = account_store.apply_quiz_result(username=username, result=result, quiz=quiz)
        dashboard = account_store.build_dashboard_data(username=username, limit=30)
        dashboard_path = account_out_dir / f"dashboard_{username}.json"
        _save_json(dashboard_path, dashboard)
        print(
            "Bot: Points updated -> "
            f"+{update.quiz_points} this quiz, total {update.total_points} "
            f"(quizzes: {update.total_quizzes}, correct: {update.total_correct}/{update.total_answered})\n"
        )
        print(f"Bot: Dashboard data updated -> {dashboard_path}\n")
        while True:
            follow_up = input(
                "Bot: Type 'continue' for another quiz, 'review' to review, 'dashboard' to see stats, or 'quit' to exit: "
            ).strip().lower()
            if follow_up in {"quit", "exit", "/quit", "/exit"}:
                print("Bye.")
                return
            if follow_up in {"continue", "c", "yes", "y"}:
                print()
                break
            if _is_dashboard_request(follow_up):
                print(_format_dashboard_text(account_store=account_store, username=username))
                print()
                continue
            if _is_review_request(follow_up):
                _print_post_quiz_review(result)
                continue
            print("Bot: Please type 'continue', 'review', 'dashboard', or 'quit'.")


def _build_intent_router() -> QuizIntentRouter | None:
    try:
        settings = load_settings(".env")
        return QuizIntentRouter(api_key=settings.deepseek_api_key, model=settings.deepseek_model, base_url=settings.deepseek_base_url)
    except Exception:
        return None


def _route_top_level_action(*, prompt: str, intent_router: QuizIntentRouter | None) -> dict | None:
    if intent_router is None:
        return None
    lowered = prompt.strip().lower()
    if lowered.startswith("/"):
        return None
    try:
        resp = intent_router.client.chat.completions.create(
            model=intent_router.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You route top-level chatbot intents. "
                        "Use build_quiz for normal quiz requests (e.g. 'quiz me on X', 'give me questions on Y'). "
                        "Use lesson actions for lesson navigation. "
                        "Use ask when the student is asking a general question, seeking explanations, or seeking tutor help (e.g. 'what is a BST?')."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            tools=TOP_LEVEL_TOOL_SPEC,
            tool_choice={"type": "function", "function": {"name": "route_top_level_intent"}},
            stream=False,
            timeout=4.0,
        )
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            return None
        args_text = tool_calls[0].function.arguments or "{}"
        payload = json.loads(args_text)
        if not isinstance(payload, dict):
            return None
        return payload
    except Exception:
        return None


def _handle_routed_top_level_action(
    *,
    routed: dict,
    username: str,
    account_store: AccountStore,
    intent_router: QuizIntentRouter | None,
    output_dir: Path,
    prompt: str = "",
) -> bool:
    action = str(routed.get("action", "")).strip().lower()
    selector = routed.get("selector")
    topic = routed.get("topic")
    if action in {"", "build_quiz"}:
        return False
    if action == "exit":
        print("Bye.")
        raise SystemExit(0)
    if action == "dashboard":
        print(_format_dashboard_text(account_store=account_store, username=username))
        print()
        return True
    if action == "ask":
        _answer_general_question(prompt, intent_router)
        return True
    if action == "lesson_status":
        print(_format_lesson_status(account_store=account_store, username=username))
        print()
        return True
    if action == "list_lessons":
        print(_format_lessons_library(account_store=account_store, username=username))
        print()
        return True
    if action == "continue_lesson":
        _run_active_lesson_checkpoint(
            username=username,
            account_store=account_store,
            intent_router=intent_router,
            output_dir=output_dir,
        )
        return True
    if action == "start_lesson":
        topic_text = str(topic or "").strip()
        if not topic_text:
            return False
        _start_lesson_from_prompt(
            prompt=f"start lesson on {topic_text}",
            username=username,
            account_store=account_store,
            intent_router=intent_router,
            output_dir=output_dir,
        )
        return True
    if action in {"open_lesson", "resume_lesson", "restart_lesson"}:
        selector_text = str(selector or "").strip()
        if not selector_text:
            return False
        lesson_id = _resolve_lesson_selector(account_store=account_store, username=username, selector=selector_text)
        if lesson_id is None:
            print("Bot: Lesson not found. Run 'lessons' to see available entries.\n")
            return True
        if action == "restart_lesson":
            confirm = input(f"Confirm restart of '{lesson_id}'? Type 'yes' to continue: ").strip().lower()
            if confirm != "yes":
                print("Bot: Restart cancelled.\n")
                return True
            account_store.restart_lesson(username=username, lesson_id=lesson_id)
            print(f"Bot: Lesson '{lesson_id}' restarted.\n")
            return True
        account_store.set_active_lesson(username=username, lesson_id=lesson_id)
        _run_active_lesson_checkpoint(
            username=username,
            account_store=account_store,
            intent_router=intent_router,
            output_dir=output_dir,
        )
        return True
    return False


def _answer_general_question(prompt: str, intent_router: QuizIntentRouter | None) -> None:
    if intent_router is None:
        print("Bot: I'm currently offline, so I can only help with local quizzes. Please ask to start a quiz or lesson.\n")
        return

    print("Bot: Thinking...")
    try:
        resp = intent_router.client.chat.completions.create(
            model=intent_router.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are Obital, an expert educational tutor. Answer the student's question directly, clearly, and concisely (within 3-6 sentences)."
                },
                {"role": "user", "content": prompt}
            ],
            stream=False,
            timeout=8.0,
        )
        content = (resp.choices[0].message.content or "").strip()
        if content:
            print(f"Bot: {content}\n")
        else:
            print("Bot: I'm sorry, I couldn't generate an answer. Try rephrasing your question.\n")
    except Exception as exc:
        print(f"Bot: Tutor request failed ({exc}).\n")


def _is_general_question_heuristic(prompt: str) -> bool:
    lowered = prompt.strip().lower()
    if lowered.endswith("?"):
        return True
    question_words = ("what is", "what are", "how do", "how does", "why is", "why does", "explain ", "tell me about")
    return any(lowered.startswith(w) for w in question_words)


def _prompt_username() -> str:
    while True:
        raw = input("Username: ").strip()
        if not raw:
            print("Please enter a username.")
            continue
        clean = " ".join(raw.split())
        if len(clean) > 40:
            clean = clean[:40]
        return clean.lower()


def _prompt_pin(label: str = "PIN") -> str:
    while True:
        pin = input(f"{label}: ").strip()
        if len(pin) < 4 or len(pin) > 12 or not pin.isdigit():
            print("PIN must be 4-12 digits.")
            continue
        return pin


def _auth_flow(store: AccountStore) -> tuple[str, dict]:
    print("Account: type 'create' to register or 'login' to sign in.")
    while True:
        mode = input("Auth mode (create/login): ").strip().lower()
        if mode not in {"create", "login"}:
            print("Please type 'create' or 'login'.")
            continue
        username = _prompt_username()
        if mode == "create":
            if store.user_exists(username):
                print("Username already exists. Please login or pick another username.")
                continue
            pin = _prompt_pin("Set PIN")
            confirm = _prompt_pin("Confirm PIN")
            if pin != confirm:
                print("PIN mismatch. Try again.")
                continue
            profile = store.create_user(username=username, pin=pin)
            print("Account created.\n")
            return username, profile
        # login
        if not store.user_exists(username):
            print("Username not found. Create it first.")
            continue
        pin = _prompt_pin("PIN")
        profile = store.verify_login(username=username, pin=pin)
        if profile is None:
            print("Invalid PIN.")
            continue
        print("Login successful.\n")
        return username, profile


def _load_qa_output(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    from engine.pipeline.models import CurriculumQAOutput

    return CurriculumQAOutput.model_validate(payload)


def _save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _is_review_request(text: str) -> bool:
    return any(
        token in text
        for token in {
            "review",
            "check my answers",
            "review my answers",
            "show answers",
            "explain mistakes",
            "mistakes",
        }
    )


def _is_dashboard_request(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in {"/dashboard", "dashboard"}:
        return True
    return any(
        token in lowered
        for token in {
            "show my dashboard",
            "show me my dashboard",
            "my dashboard",
            "my stats",
            "show stats",
            "my progress",
            "show progress",
            "my performance",
            "dashboard",
        }
    )


def _print_post_quiz_review(result) -> None:
    print("\n=== Post-Quiz Review ===")
    print(f"Score: {result.correct_answers}/{result.total_questions} ({result.score_percent}%)")
    for item in result.results:
        status = "OK" if item.is_correct else "NO"
        print(f"- {item.question_id}: {status} ({item.score})")
        print(f"  Your answer: {item.submitted_answer}")
        print(f"  Expected: {item.expected_answer}")
    print()


def _format_dashboard_text(*, account_store: AccountStore, username: str, limit: int = 10) -> str:
    data = account_store.build_dashboard_data(username=username, limit=limit)
    summary = data.get("summary", {})
    lines = [
        "=== Dashboard ===",
        f"User: {data.get('username', username)}",
        (
            "Totals: "
            f"points={summary.get('total_points', 0)} | "
            f"quizzes={summary.get('total_quizzes', 0)} | "
            f"accuracy={summary.get('accuracy_percent', 0.0)}% | "
            f"answered={summary.get('total_answered', 0)}"
        ),
        f"Updated at: {summary.get('updated_at')}",
        "Recent trend (score%):",
    ]
    trend = data.get("trend", [])
    if not trend:
        lines.append("- No quiz history yet. Submit quizzes to build trend data.")
        return "\n".join(lines)

    for item in trend:
        score = float(item.get("score_percent", 0.0))
        bars = "#" * max(1, min(20, int(round(score / 5.0))))
        lines.append(
            f"- {item.get('quiz_id', '')}: {score:.2f}% [{bars}] (+{int(item.get('points_earned', 0))} pts)"
        )
    return "\n".join(lines)


def _save_router_telemetry(
    *,
    intent_router: QuizIntentRouter | None,
    username: str,
    quiz_id: str,
    session_status: str,
    output_dir: Path,
) -> None:
    if intent_router is None:
        return
    captured_at = datetime.now(timezone.utc).isoformat()
    telemetry = intent_router.telemetry_snapshot()
    payload = {
        "username": username,
        "quiz_id": quiz_id,
        "session_status": session_status,
        "captured_at": captured_at,
        "telemetry": telemetry,
    }
    latest_path = output_dir / f"router_telemetry_{username}.json"
    _save_json(latest_path, payload)

    history_path = output_dir / f"router_telemetry_history_{username}.json"
    history_payload = {"events": []}
    if history_path.exists():
        try:
            loaded = json.loads(history_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("events"), list):
                history_payload = loaded
        except Exception:
            history_payload = {"events": []}
    events = history_payload.setdefault("events", [])
    events.append(payload)
    if len(events) > 500:
        del events[:-500]
    _save_json(history_path, history_payload)


def _failure_hint(reason: str) -> str:
    if reason == "exclude_eliminated_all":
        return "Your exclude filter removed all matching topics. Try a broader topic or remove the exclusion."
    if reason == "insufficient_after_filters":
        return "Not enough questions matched your filters. Reduce count or loosen topic/difficulty constraints."
    if reason == "generation_cap_reached":
        return "Fallback generation hit safety limits. Reduce requested count or loosen constraints."
    if reason == "no_questions_available":
        return "No questions are available for this request. Try a different topic or regenerate your QA dataset."
    return "Try relaxing filters or changing topic/difficulty."


def _is_start_lesson_request(text: str) -> bool:
    return _extract_lesson_topic(text) is not None


def _is_continue_lesson_request(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered in {"continue lesson", "resume lesson"}


def _is_resume_lesson_request(text: str) -> bool:
    lowered = text.strip().lower()
    return bool(re.match(r"^resume(?:\s+lesson)?\s+(.+)$", lowered))


def _is_lessons_list_request(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered in {"lessons", "my lessons", "show lessons", "lesson library"}


def _is_open_lesson_request(text: str) -> bool:
    # Backward-compat alias kept for older user muscle memory.
    # UX surfaces resume/restart as primary actions.
    lowered = text.strip().lower()
    return bool(re.match(r"^open\s+lesson\s+(.+)$", lowered))


def _is_restart_lesson_request(text: str) -> bool:
    lowered = text.strip().lower()
    return bool(re.match(r"^restart\s+lesson\s+(.+)$", lowered))


def _is_lesson_status_request(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered in {"lesson status", "show lesson status", "my lesson status"}


def _handle_lesson_command(
    *,
    prompt: str,
    username: str,
    account_store: AccountStore,
    intent_router: QuizIntentRouter | None,
    output_dir: Path,
) -> bool:
    """Handle lesson commands.

    .. deprecated::
        Use lesson harness via POST /lessons/generate instead. This function handles
        old-style lesson commands without embedded quizzes.
    """
    import warnings
    warnings.warn(
        "_handle_lesson_command is deprecated. Use lesson harness via POST /lessons/generate instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    lowered = prompt.strip().lower()
    if lowered in {"start lesson", "start a lesson"}:
        print("Bot: Use format: start lesson on <topic>.\n")
        return True
    if _is_lesson_status_request(prompt):
        print(_format_lesson_status(account_store=account_store, username=username))
        print()
        return True
    if _is_lessons_list_request(prompt):
        print(_format_lessons_library(account_store=account_store, username=username))
        print()
        return True
    if _is_open_lesson_request(prompt):
        selector = _extract_lesson_selector(prompt)
        if selector is None:
            print("Bot: Use format: open lesson <number|lesson_id>.\n")
            return True
        lesson_id = _resolve_lesson_selector(account_store=account_store, username=username, selector=selector)
        if lesson_id is None:
            print("Bot: Lesson not found. Run 'lessons' to see available entries.\n")
            return True
        account_store.set_active_lesson(username=username, lesson_id=lesson_id)
        _run_active_lesson_checkpoint(
            username=username,
            account_store=account_store,
            intent_router=intent_router,
            output_dir=output_dir,
        )
        return True
    if _is_continue_lesson_request(prompt):
        lesson = account_store.get_active_lesson(username=username)
        if lesson is None:
            lessons = account_store.list_lessons(username=username)
            candidates = [l for l in lessons if l.get("status") == "in_progress"]
            if len(candidates) == 1:
                account_store.set_active_lesson(username=username, lesson_id=str(candidates[0]["lesson_id"]))
            elif len(candidates) > 1:
                print("Bot: Multiple in-progress lessons found. Use 'resume <number|lesson_id>'.")
                print(_format_lessons_library(account_store=account_store, username=username))
                print()
                return True
        _run_active_lesson_checkpoint(
            username=username,
            account_store=account_store,
            intent_router=intent_router,
            output_dir=output_dir,
        )
        return True
    if _is_resume_lesson_request(prompt):
        selector = _extract_lesson_selector(prompt)
        if selector is None:
            print("Bot: Use format: resume <number|lesson_id>.\n")
            return True
        lesson_id = _resolve_lesson_selector(account_store=account_store, username=username, selector=selector)
        if lesson_id is None:
            print("Bot: Lesson not found. Run 'lessons' to see available entries.\n")
            return True
        account_store.set_active_lesson(username=username, lesson_id=lesson_id)
        _run_active_lesson_checkpoint(
            username=username,
            account_store=account_store,
            intent_router=intent_router,
            output_dir=output_dir,
        )
        return True
    if _is_restart_lesson_request(prompt):
        selector = _extract_lesson_selector(prompt)
        if selector is None:
            print("Bot: Use format: restart lesson <number|lesson_id>.\n")
            return True
        lesson_id = _resolve_lesson_selector(account_store=account_store, username=username, selector=selector)
        if lesson_id is None:
            print("Bot: Lesson not found. Run 'lessons' to see available entries.\n")
            return True
        confirm = input(f"Confirm restart of '{lesson_id}'? Type 'yes' to continue: ").strip().lower()
        if confirm != "yes":
            print("Bot: Restart cancelled.\n")
            return True
        account_store.restart_lesson(username=username, lesson_id=lesson_id)
        print(f"Bot: Lesson '{lesson_id}' restarted.\n")
        return True
    if _is_start_lesson_request(prompt):
        _start_lesson_from_prompt(
            prompt=prompt,
            username=username,
            account_store=account_store,
            intent_router=intent_router,
            output_dir=output_dir,
        )
        return True
    return False


def _extract_lesson_topic(prompt: str) -> str | None:
    lowered = prompt.strip().lower()
    patterns = [
        r"\bstart\s+lesson\s+(?:on|about)\s+(.+)$",
        r"\bgive\s+me\s+a\s+lesson\s+(?:on|about)\s+(.+)$",
        r"\blesson\s+(?:on|about)\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if not match:
            continue
        topic = match.group(1).strip(" .!?")
        if topic:
            return topic
    return None


def _extract_lesson_selector(prompt: str) -> str | None:
    # Accepted command forms:
    # - open lesson <n|lesson_id>
    # - restart lesson <n|lesson_id>
    # - resume <n|lesson_id>
    # - resume lesson <n|lesson_id>
    raw = prompt.strip()
    lowered = raw.lower()
    patterns = [
        r"^open\s+lesson\s+(.+)$",
        r"^restart\s+lesson\s+(.+)$",
        r"^resume\s+lesson\s+(.+)$",
        r"^resume\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, lowered)
        if match:
            out = match.group(1).strip()
            return out if out else None
    return None


def _resolve_lesson_selector(*, account_store: AccountStore, username: str, selector: str) -> str | None:
    lessons = account_store.list_lessons(username=username)
    if not lessons:
        return None
    if selector.isdigit():
        idx = int(selector)
        if 1 <= idx <= len(lessons):
            return str(lessons[idx - 1]["lesson_id"])
        return None
    selector_clean = selector.strip().lower()
    for entry in lessons:
        lesson_id = str(entry.get("lesson_id", "")).lower()
        if lesson_id == selector_clean:
            return str(entry.get("lesson_id", ""))
    return None


def _default_lesson_checkpoints(topic: str) -> list[dict]:
    """Return default checkpoints for a lesson.

    .. deprecated::
        Use lesson harness with embedded quizzes instead. This function creates
        checkpoints without quiz data, requiring fallback to QA pool.
    """
    import warnings
    warnings.warn(
        "_default_lesson_checkpoints is deprecated. Use lesson harness with embedded quizzes instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return [
        {"title": f"{topic} fundamentals", "difficulty": "easy", "num_questions": 2, "pass_threshold_percent": 60.0, "question_mode": "mcq"},
        {"title": f"{topic} application", "difficulty": "medium", "num_questions": 3, "pass_threshold_percent": 70.0, "question_mode": "mcq"},
        {"title": f"{topic} mastery", "difficulty": "hard", "num_questions": 3, "pass_threshold_percent": 75.0, "question_mode": "mcq"},
    ]


def _start_lesson_from_prompt(
    *,
    prompt: str,
    username: str,
    account_store: AccountStore,
    intent_router: QuizIntentRouter | None,
    output_dir: Path,
) -> None:
    """Start a lesson from a prompt.

    .. deprecated::
        Use lesson harness via POST /lessons/generate instead. This function creates
        lessons without embedded quizzes, requiring fallback to QA pool.
    """
    import warnings
    warnings.warn(
        "_start_lesson_from_prompt is deprecated. Use lesson harness via POST /lessons/generate instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    topic = _extract_lesson_topic(prompt)
    if not topic:
        print("Bot: Use format: start lesson on <topic>.\n")
        return
    lesson_id = f"lesson_{re.sub(r'[^a-z0-9]+', '_', topic.lower()).strip('_')}"
    checkpoints = _default_lesson_checkpoints(topic)
    account_store.start_lesson(
        username=username,
        lesson_id=lesson_id,
        title=f"Lesson: {topic}",
        topic=topic,
        checkpoints=checkpoints,
    )
    print(f"Bot: Started lesson '{lesson_id}' on topic '{topic}'.")
    print("Bot: Note: Use the API endpoint POST /lessons/generate for richer lessons with embedded quizzes.")
    print()
    _run_active_lesson_checkpoint(
        username=username,
        account_store=account_store,
        intent_router=intent_router,
        output_dir=output_dir,
    )


def _run_active_lesson_checkpoint(
    *,
    username: str,
    account_store: AccountStore,
    intent_router: QuizIntentRouter | None,
    output_dir: Path,
) -> None:
    lesson = account_store.get_active_lesson(username=username)
    if not lesson:
        print("Bot: No active lesson. Start one with: start lesson on <topic>.\n")
        return
    lesson_id = str(lesson.get("lesson_id", ""))
    topic = str(lesson.get("topic", "")).strip()
    checkpoints = lesson.get("checkpoints", [])
    if not isinstance(checkpoints, list) or not checkpoints:
        print("Bot: Lesson has no checkpoints configured.\n")
        return
    idx = int(lesson.get("current_checkpoint_index", 0))
    if idx >= len(checkpoints):
        print("Bot: Lesson already completed.\n")
        return
    checkpoint = checkpoints[idx]
    normalized = _normalize_lesson_checkpoint(checkpoint)
    if normalized is None:
        print("Bot: Lesson checkpoint config is invalid. Start a new lesson.\n")
        return
    difficulty = normalized["difficulty"]
    num_questions = normalized["num_questions"]
    threshold = normalized["pass_threshold_percent"]
    print(
        "Bot: Lesson checkpoint -> "
        f"{idx + 1}/{len(checkpoints)} '{checkpoint.get('title', 'checkpoint')}', "
        f"difficulty={difficulty}, target={threshold}%."
    )

    # Use embedded quiz data from lesson harness if available
    embedded_quiz = checkpoint.get("quiz")
    if isinstance(embedded_quiz, list) and embedded_quiz:
        quiz_id = hashlib.sha256(f"{lesson_id}::checkpoint_{idx}".encode()).hexdigest()[:16]
        questions = []
        for q_idx, q in enumerate(embedded_quiz):
            if not isinstance(q, dict) or not q.get("question"):
                continue
            questions.append(QuizQuestion(
                question_id=str(q.get("question_id", f"{topic}_{q_idx + 1}")),
                topic_id=str(q.get("topic_id", "")),
                topic_title=str(q.get("topic_title", topic)),
                question=str(q.get("question", "")),
                expected_answer=str(q.get("expected_answer", "")),
                difficulty=str(q.get("difficulty", difficulty)),
                question_mode=str(q.get("question_mode", normalized["question_mode"])),
                options=q.get("options") if isinstance(q.get("options"), list) else None,
                correct_option_index=q.get("correct_option_index"),
                source_context=q.get("source_context"),
            ))
        if not questions:
            print("Bot: Embedded quiz data is empty for this checkpoint.\n")
            return
        quiz = QuizBuildResponse(
            quiz_id=quiz_id,
            topic_filter=topic,
            questions=questions,
        )
        print(f"Bot: Using embedded quiz from lesson harness ({len(questions)} questions).")
    else:
        print(
            "Bot: This lesson checkpoint has no embedded quiz data. "
            "Regenerate the lesson with the backend lesson harness and try again.\n"
        )
        return

    _save_json(output_dir / "quiz_built.json", quiz.model_dump())
    print(
        f"Bot: Built checkpoint quiz '{quiz.quiz_id}' with {len(quiz.questions)} questions "
        f"(status: {quiz.build_indicator})."
    )
    if quiz.warnings:
        for warning in quiz.warnings:
            print(f"Bot warning: {warning}")
    if quiz.build_indicator == "failed" or not quiz.questions:
        print(f"Bot: Lesson checkpoint build failed ({quiz.failure_reason}).")
        print(f"Bot: {_failure_hint(quiz.failure_reason)}\n")
        return
    try:
        result = run_quiz_session(
            quiz,
            intent_router=intent_router,
            dashboard_handler=lambda: _format_dashboard_text(account_store=account_store, username=username),
        )
    except RuntimeError as exc:
        if str(exc) == "Quiz session quit by user.":
            _save_router_telemetry(
                intent_router=intent_router,
                username=username,
                quiz_id=quiz.quiz_id,
                session_status="cancelled",
                output_dir=output_dir,
            )
            print("Bot: Checkpoint cancelled. Use 'continue lesson' to retry.\n")
            return
        raise
    _save_router_telemetry(
        intent_router=intent_router,
        username=username,
        quiz_id=result.quiz_id,
        session_status="submitted",
        output_dir=output_dir,
    )
    _save_json(output_dir / "quiz_result.json", result.model_dump())
    update = account_store.apply_quiz_result(username=username, result=result, quiz=quiz)
    passed = float(result.score_percent) >= threshold
    state = account_store.apply_lesson_attempt(
        username=username,
        lesson_id=lesson_id,
        checkpoint_index=idx,
        quiz_id=result.quiz_id,
        score_percent=float(result.score_percent),
        passed=passed,
    )
    print(
        "Bot: Points updated -> "
        f"+{update.quiz_points} this quiz, total {update.total_points} "
        f"(quizzes: {update.total_quizzes}, correct: {update.total_correct}/{update.total_answered})"
    )
    if passed:
        print(f"Bot: Checkpoint passed ({result.score_percent}%).")
    else:
        print(f"Bot: Checkpoint not passed ({result.score_percent}%). Use 'continue lesson' to retry.")
    if bool(state.get("completed", False)):
        print("Bot: Lesson completed.\n")
    else:
        next_idx = int(state.get("current_checkpoint_index", 0)) + 1
        total = len(checkpoints)
        print(f"Bot: Lesson progress -> next checkpoint {min(next_idx, total)}/{total}.\n")


def _format_lesson_status(*, account_store: AccountStore, username: str) -> str:
    progress = account_store.get_lesson_progress(username=username)
    active = progress.get("active_lesson_id")
    lessons = progress.get("lessons", {})
    lines = ["=== Lesson Status ==="]
    lines.append(f"Active lesson: {active or 'none'}")
    if not isinstance(lessons, dict) or not lessons:
        lines.append("No lesson history yet.")
        return "\n".join(lines)
    for lesson_id, state in lessons.items():
        if not isinstance(state, dict):
            continue
        idx = int(state.get("current_checkpoint_index", 0))
        checkpoints = state.get("checkpoints", [])
        total = len(checkpoints) if isinstance(checkpoints, list) else 0
        completed = bool(state.get("completed", False))
        status = "completed" if completed else "in-progress"
        if completed:
            lines.append(f"- {lesson_id}: completed ({max(1, total)}/{max(1, total)} checkpoints)")
        else:
            lines.append(f"- {lesson_id}: in-progress, checkpoint {min(idx + 1, max(1, total))}/{max(1, total)}")
    return "\n".join(lines)


def _format_lessons_library(*, account_store: AccountStore, username: str) -> str:
    lessons = account_store.list_lessons(username=username)
    lines = ["=== Lessons ==="]
    if not lessons:
        lines.append("No lessons found. Start one with: start lesson on <topic>.")
        return "\n".join(lines)
    for idx, lesson in enumerate(lessons, start=1):
        lesson_id = str(lesson.get("lesson_id", ""))
        title = str(lesson.get("title", lesson_id))
        status = str(lesson.get("status", "in_progress"))
        current = int(lesson.get("current_checkpoint", 0))
        total = max(1, int(lesson.get("total_checkpoints", 0)))
        lines.append(f"{idx}. [{status}] {title} - checkpoint {min(current + 1, total)}/{total}")
    lines.append("Use the number on the left:")
    lines.append("resume 1, restart lesson 1")
    return "\n".join(lines)


def _normalize_lesson_checkpoint(checkpoint: object) -> dict[str, object] | None:
    if not isinstance(checkpoint, dict):
        return None
    raw_difficulty = str(checkpoint.get("difficulty", "medium")).strip().lower()
    difficulty = raw_difficulty if raw_difficulty in {"easy", "medium", "hard"} else "medium"
    try:
        num_questions = int(checkpoint.get("num_questions", 3))
    except (TypeError, ValueError):
        return None
    if num_questions < 1 or num_questions > 50:
        return None
    try:
        threshold = float(checkpoint.get("pass_threshold_percent", 70.0))
    except (TypeError, ValueError):
        return None
    if threshold < 0.0 or threshold > 100.0:
        return None
    title = str(checkpoint.get("title", "checkpoint")).strip() or "checkpoint"
    raw_mode = str(checkpoint.get("question_mode", "mcq")).strip().lower()
    question_mode = raw_mode if raw_mode in {"open", "mcq"} else "mcq"
    return {
        "title": title,
        "difficulty": difficulty,
        "num_questions": num_questions,
        "pass_threshold_percent": threshold,
        "question_mode": question_mode,
    }


if __name__ == "__main__":
    run()
