# Authored by Noah — https://github.com/noah-e1528170
from __future__ import annotations

import itertools
import json
import logging
import re
import time
from collections import deque
from functools import lru_cache
from pathlib import Path
from threading import Lock

from backend.app.schemas import ChatMessage, ChatSettingsPatch, Doc, Lesson, SettingsPatchRequest
from backend.app.services.agent_harness import run_agent_turn, run_agent_turn_stream, strip_dsml
from backend.app.services.chat_tools import (
    create_lesson_payload,
    submit_answer_payload,
    submit_quiz_payload,
    review_quiz_payload,
    exit_quiz_payload,
)
from backend.app.services.settings_service import get_effective_system_prompt, get_settings, update_settings
from backend.app.services.storage import get_rewards_dashboard, list_account_lessons, list_lessons, list_docs, search_chunks_by_vector
from engine.pipeline.config import load_settings
from engine.quiz.account_system import AccountStore
from engine.quiz.quiz_tui import QuizIntentRouter

logger = logging.getLogger(__name__)

_RAG_TOP_K = 3
_RAG_THRESHOLD = 0.35

_WIKI_MAX_CALLS = 10
_WIKI_WINDOW_SEC = 60.0
_wiki_call_timestamps: deque[float] = deque()


def _wiki_rate_ok() -> bool:
    now = time.monotonic()
    while _wiki_call_timestamps and _wiki_call_timestamps[0] < now - _WIKI_WINDOW_SEC:
        _wiki_call_timestamps.popleft()
    if len(_wiki_call_timestamps) >= _WIKI_MAX_CALLS:
        return False
    _wiki_call_timestamps.append(now)
    return True


def _search_wikipedia(query: str) -> list[str]:
    if not _wiki_rate_ok():
        return []
    try:
        import requests
        resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 3,
                "format": "json",
            },
            headers={"User-Agent": "Obital/1.0 (https://github.com/anomalyco/opencode)"},
            timeout=5,
        )
        resp.raise_for_status()
        hits = resp.json().get("query", {}).get("search", [])
        results: list[str] = []
        for hit in hits[:3]:
            snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", "")).strip()
            title = hit.get("title", "")
            if snippet:
                results.append(f"{title}: {snippet}")
        return results
    except Exception as exc:
        logger.warning("Wikipedia search failed: %s", exc)
        return []


_DSML_MOJIBAKE_BARS = "\u00ef\u00bd\u0153\u00ef\u00bd\u0153"
_DSML_FULLWIDTH_BARS = "\uff5c\uff5c"
_DSML_BAR_RE = rf"(?:{re.escape(_DSML_MOJIBAKE_BARS)}|{re.escape(_DSML_FULLWIDTH_BARS)})"
_DSML_PREFIX_RE = rf"{_DSML_BAR_RE}DSML{_DSML_BAR_RE}"
_DSML_RE = re.compile(rf"<{_DSML_PREFIX_RE}.*?>", re.DOTALL)
_HAS_DSML = re.compile(rf"<{_DSML_PREFIX_RE}")
_DSML_INVOKE_RE = re.compile(
    rf"<{_DSML_PREFIX_RE}invoke\s+name=\"([^\"]+)\"\s*>(.*?)</{_DSML_PREFIX_RE}invoke>",
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    rf"<{_DSML_PREFIX_RE}parameter\s+name=\"([^\"]+)\"[^>]*>(.*?)</{_DSML_PREFIX_RE}parameter>",
    re.DOTALL,
)

@lru_cache(maxsize=128)
def _cached_embed_text(text: str) -> tuple[float, ...]:
    from backend.app.services.embeddings import embed_text
    return tuple(embed_text(text))


def _search_relevant_chunks(
    query: str,
    account_id: str,
    user_dek: bytes | None,
    query_vector: list[float] | None = None,
) -> list[str]:
    try:
        if query_vector:
            vector = query_vector
        else:
            from backend.app.services.embeddings import server_embeddings_enabled
            if not server_embeddings_enabled():
                logger.info("Skipping semantic RAG search; client query_vector was not provided.")
                return []
            vector = list(_cached_embed_text(query))
        results = search_chunks_by_vector(
            query_vector=vector,
            top_k=_RAG_TOP_K,
            threshold=_RAG_THRESHOLD,
            account_id=account_id,
            user_dek=user_dek,
        )
        return [r["text"] for r in results if "text" in r]
    except Exception as exc:
        logger.warning("RAG search failed: %s", exc)
        return []


_router_lock = Lock()
_router: QuizIntentRouter | None = None
_router_unavailable_reason: str | None = None
CHAT_TOOL_TIMEOUT_SEC = 12.0
MAX_TOOL_CALLS_PER_TURN = 5
_dsml_call_counter = itertools.count()
BASE_DIR = Path(__file__).resolve().parents[3]
_AGENT_SKILL_PATH = BASE_DIR / "backend" / "prompts" / "agent_skill.md"
_BASE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_system_prompt",
            "description": "Get current system prompt.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_system_prompt",
            "description": "Update system prompt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "system_prompt": {"type": "string"},
                },
                "required": ["system_prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dashboard_summary",
            "description": "Get dashboard: points, quizzes, accuracy.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_lessons",
            "description": "List lessons with progress.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_docs",
            "description": "List uploaded documents.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_doc_excerpt",
            "description": "Read text content from an uploaded document. Use this when the student mentions a document or you need to read a document to answer questions about it. Returns up to 3000 characters of text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_index": {"type": "integer"},
                    "doc_name": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_lesson",
            "description": "Start a lesson on a topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_wikipedia",
            "description": "Search Wikipedia for a topic. Returns short snippets from relevant articles. Use when you need external knowledge not in the student's documents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
]

_QUIZ_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "submit_answer",
            "description": "Submit quiz answer. Use when student gives an answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                },
                "required": ["answer"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_quiz",
            "description": "Show quiz progress.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exit_quiz",
            "description": "End quiz early.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_quiz",
            "description": "Grade and finalize quiz.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

CHAT_TOOL_SPEC = _BASE_TOOLS + _QUIZ_TOOLS


def _build_tool_spec(account_id: str) -> list[dict]:
    """Only include quiz tools when there's an active quiz."""
    from engine.quiz.account_system import AccountStore
    store = AccountStore(BASE_DIR / "engine" / "accounts" / "accounts.json")
    if store.get_active_quiz(username=account_id) is not None:
        return _BASE_TOOLS + _QUIZ_TOOLS
    return list(_BASE_TOOLS)


def get_router() -> QuizIntentRouter | None:
    global _router, _router_unavailable_reason
    if _router is not None:
        return _router
    with _router_lock:
        if _router is not None:
            return _router
        try:
            settings = load_settings(".env")
            _router = QuizIntentRouter(api_key=settings.deepseek_api_key, model=settings.deepseek_model, base_url=settings.deepseek_base_url)
            _router.thinking_enabled = settings.deepseek_thinking_enabled
            _router.reasoning_effort = settings.deepseek_reasoning_effort
            return _router
        except Exception as exc:
            _router_unavailable_reason = str(exc)
            return None


def _strip_dsml(text: str) -> str:
    """Remove all DSML tags from text. Returns cleaned text."""
    if not text:
        return ""
    if _HAS_DSML.search(text):
        cleaned = _DSML_RE.sub("", text).strip()
        # Catch-all: remove any remaining partial DSML markers
        cleaned = re.sub(r'<｜｜DSML｜｜[^>]*', "", cleaned)
        cleaned = re.sub(r'</｜｜DSML｜｜[^>]*', "", cleaned)
        return cleaned
    return text


def _apply_system_prompt(messages: list[dict[str, str]], user_system_prompt: str, dialectic_block: str | None = None) -> list[dict[str, str]]:
    agent_skill = _load_agent_skill()
    merged_prompt = user_system_prompt
    if agent_skill and agent_skill not in merged_prompt:
        merged_prompt = f"{merged_prompt}\n\n{agent_skill}"
    if dialectic_block:
        merged_prompt = f"{merged_prompt}\n\n{dialectic_block}"
    merged_prompt = merged_prompt.strip()
    if not messages:
        return [{"role": "system", "content": merged_prompt}]
    updated = list(messages)
    if updated[0].get("role") == "system":
        # Merge: context builder's preamble + user's custom prompt
        base = updated[0]["content"]
        if merged_prompt and merged_prompt not in base:
            updated[0] = {"role": "system", "content": f"{base}\n\n{merged_prompt}"}
    else:
        updated.insert(0, {"role": "system", "content": merged_prompt})
    return updated


@lru_cache(maxsize=1)
def _load_agent_skill() -> str:
    try:
        return _AGENT_SKILL_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _tool_result_payload(name: str, arguments: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    if name == "get_system_prompt":
        current = get_settings(account_id=account_id, user_dek=user_dek).chat.system_prompt
        return json.dumps({"system_prompt": current}, ensure_ascii=True)

    if name == "update_system_prompt":
        raw_prompt = arguments.get("system_prompt", "")
        if not isinstance(raw_prompt, str):
            raise ValueError("system_prompt must be a string")
        updated = update_settings(
            account_id=account_id,
            patch=SettingsPatchRequest(chat=ChatSettingsPatch(system_prompt=raw_prompt)),
            user_dek=user_dek,
        ).chat.system_prompt
        return json.dumps({"status": "updated", "system_prompt": updated}, ensure_ascii=True)

    if name == "get_dashboard_summary":
        data = get_rewards_dashboard(account_id=account_id, limit=5, user_dek=user_dek)
        summary = data.get("summary", {})
        return json.dumps({
            "total_points": summary.get("total_points", 0),
            "total_quizzes": summary.get("total_quizzes", 0),
            "accuracy_percent": summary.get("accuracy_percent", 0),
            "total_correct": summary.get("total_correct", 0),
            "total_answered": summary.get("total_answered", 0),
        }, ensure_ascii=True)

    if name == "list_lessons":
        all_lessons = list_account_lessons(account_id=account_id, user_dek=user_dek)
        items = []
        for i, l in enumerate(all_lessons, 1):
            items.append({
                "index": i,
                "id": l.id,
                "title": l.title,
                "objective": l.objective,
                "status": l.status,
                "current_checkpoint": l.current_checkpoint,
                "total_checkpoints": l.total_checkpoints,
            })
        return json.dumps({"lessons": items}, ensure_ascii=True)

    if name == "list_docs":
        docs = list_docs(account_id=account_id, user_dek=user_dek)
        items = []
        for i, d in enumerate(docs, 1):
            items.append({
                "index": i,
                "name": d.name,
                "chunk_count": len(d.chunks) if d.chunks else 0,
            })
        return json.dumps({"documents": items}, ensure_ascii=True)

    if name in {"open_doc_excerpt", "get_doc_chunks"}:
        doc_name = arguments.get("doc_name")
        docs = list_docs(account_id=account_id, user_dek=user_dek)
        doc = None
        if doc_name:
            for d in docs:
                if d.name.lower() == str(doc_name).lower():
                    doc = d
                    break
        if doc is None:
            try:
                doc_index = int(arguments.get("doc_index", 0))
            except (TypeError, ValueError):
                doc_index = 0
            if 1 <= doc_index <= len(docs):
                doc = docs[doc_index - 1]
        if doc is None:
            raise ValueError(f"Document '{doc_name or arguments.get('doc_index')}' not found.")
        chunks = doc.chunks or []
        text = "\n\n".join(chunk.text for chunk in chunks[:5])
        return json.dumps({
            "name": doc.name,
            "excerpt": text[:3000],
            "chunk_count": len(chunks),
        }, ensure_ascii=True)

    if name == "create_lesson":
        topic = str(arguments.get("topic", "")).strip()
        if not topic:
            raise ValueError("topic is required")
        title = str(arguments.get("title", "")).strip() or f"Lesson: {topic}"
        try:
            payload = create_lesson_payload(topic=topic, title=title, account_id=account_id, use_harness=True, user_dek=user_dek)
        except RuntimeError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=True)
        return json.dumps(payload, ensure_ascii=True)

    if name == "search_wikipedia":
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("query is required")
        results = _search_wikipedia(query)
        return json.dumps({"results": results}, ensure_ascii=True)

    if name == "submit_answer":
        answer = str(arguments.get("answer", "")).strip()
        if not answer:
            raise ValueError("answer is required")
        return json.dumps(submit_answer_payload(answer=answer, account_id=account_id, user_dek=user_dek), ensure_ascii=True)

    if name == "review_quiz":
        return json.dumps(review_quiz_payload(account_id=account_id), ensure_ascii=True)

    if name == "exit_quiz":
        return json.dumps(exit_quiz_payload(account_id=account_id), ensure_ascii=True)

    if name == "submit_quiz":
        return json.dumps(submit_quiz_payload(account_id=account_id, user_dek=user_dek), ensure_ascii=True)

    raise ValueError(f"Unknown tool '{name}'")


def _parse_dsml_tool_calls(text: str) -> list[dict[str, object]]:
    tool_calls = []
    for match in _DSML_INVOKE_RE.finditer(text):
        name = match.group(1)
        body = match.group(2)
        arguments = {}
        for p_match in _DSML_PARAM_RE.finditer(body):
            p_name = p_match.group(1)
            p_val = p_match.group(2).strip()
            arguments[p_name] = p_val
        tool_calls.append({
            "id": f"call_dsml_{next(_dsml_call_counter)}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=True)
            }
        })
    return tool_calls


def _thinking_kwargs(router: QuizIntentRouter) -> dict:
    """Build extra kwargs for thinking mode if enabled."""
    if getattr(router, "thinking_enabled", False):
        return {
            "reasoning_effort": getattr(router, "reasoning_effort", "high"),
            "extra_body": {"thinking": {"type": "enabled"}},
        }
    return {}


def _complete_with_tools(
    router: QuizIntentRouter,
    *,
    messages: list[dict[str, object]],
    account_id: str,
    user_dek: bytes | None,
) -> str:
    tools = _build_tool_spec(account_id)
    response = router.client.chat.completions.create(
        model=router.model,
        messages=messages,
        tools=tools,
        tool_choice="auto",
        stream=False,
        timeout=CHAT_TOOL_TIMEOUT_SEC,
        **_thinking_kwargs(router),
    )
    message = response.choices[0].message
    tool_calls = getattr(message, "tool_calls", None) or []
    content = (getattr(message, "content", None) or "").strip()
    
    # Check for DSML tool calls in content if no standard tool calls are present
    dsml_calls = []
    if not tool_calls and ("<｜｜DSML｜｜tool_calls>" in content or "<｜｜DSML｜｜invoke" in content):
        dsml_calls = _parse_dsml_tool_calls(content)

    if not tool_calls and not dsml_calls:
        # Just clean up any stray tags in standard assistant reply
        return _strip_dsml(content)

    active_calls = tool_calls or dsml_calls

    last_tool_payload = ""
    executed_calls: list[dict[str, object]] = []
    tool_results: list[dict[str, object]] = []
    for idx, tool_call in enumerate(active_calls):
        if idx >= MAX_TOOL_CALLS_PER_TURN:
            logger.warning("Tool call limit reached (%d), stopping loop", MAX_TOOL_CALLS_PER_TURN)
            break
        if isinstance(tool_call, dict):
            name = tool_call["function"]["name"]
            raw_arguments = tool_call["function"]["arguments"]
            call_id = tool_call["id"]
            executed_calls.append(tool_call)
        else:
            function = getattr(tool_call, "function", None)
            name = getattr(function, "name", "")
            raw_arguments = getattr(function, "arguments", "") or "{}"
            call_id = getattr(tool_call, "id", "")
            executed_calls.append({
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": raw_arguments},
            })

        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            arguments = {}
        try:
            last_tool_payload = _tool_result_payload(name, arguments, account_id, user_dek)
        except Exception as exc:
            last_tool_payload = json.dumps({"error": str(exc)}, ensure_ascii=True)

        tool_results.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": last_tool_payload,
            }
        )

    followup_messages = list(messages)
    if executed_calls:
        assistant_msg: dict[str, object] = {
            "role": "assistant",
            "content": content,
            "tool_calls": executed_calls,
        }
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            assistant_msg["reasoning_content"] = reasoning
        followup_messages.append(assistant_msg)
    followup_messages.extend(tool_results)

    followup = router.client.chat.completions.create(
        model=router.model,
        messages=followup_messages,
        stream=False,
        timeout=CHAT_TOOL_TIMEOUT_SEC,
        **_thinking_kwargs(router),
    )
    final_content = (followup.choices[0].message.content or "").strip()
    
    # Clean up any leftover DSML tags in the final response if present
    final_content = _strip_dsml(final_content)

    if final_content:
        return final_content

    # If the followup returns empty, clean up the original content and return it
    return _strip_dsml(content) or last_tool_payload


def generate_reply(user_message: ChatMessage, lesson: Lesson | None, doc_texts: list[str], account_id: str = "admin", user_dek: bytes | None = None, past_messages: list[ChatMessage] | None = None, session_id: str | None = None, query_vector: list[float] | None = None) -> tuple[str, int] | str:
    router = get_router()
    system_prompt = get_effective_system_prompt(account_id=account_id, user_dek=user_dek)
    lesson_text = lesson.title if lesson else ""

    if not doc_texts:
        doc_texts = _search_relevant_chunks(user_message.text, account_id, user_dek, query_vector=query_vector)
        doc_block = "\n".join(doc_texts[:3])[:2500]
    else:
        doc_block = "\n".join(doc_texts[:10])[:8000]

    if router is not None:
        try:
            from backend.app.services.memory_service import (
                build_dialectic_context,
                get_account_store,
                get_session_summary,
            )
            store = get_account_store()
            user_profile = store.get_user_profile(account_id)
            agent_profile = store.get_agent_profile(account_id)
            session_summary = get_session_summary(session_id or "") if session_id else None
            dialectic_block = build_dialectic_context(
                user_profile=user_profile,
                agent_profile=agent_profile,
                session_summary=session_summary,
            )

            messages = router.tutor_context_builder.build_messages(
                user_payload={
                    "student_ask": user_message.text,
                    "context": doc_block or lesson_text,
                }
            )
            messages = _apply_system_prompt(messages, system_prompt, dialectic_block=dialectic_block)
            # Insert conversation history after system message, before current user message
            if past_messages:
                history = []
                for m in past_messages[-5:]:
                    role = "assistant" if m.role == "assistant" else "user"
                    history.append({"role": role, "content": m.text})
                sys_idx = next((i for i, m in enumerate(messages) if m.get("role") == "system"), -1)
                insert_at = sys_idx + 1 if sys_idx >= 0 else 0
                messages[insert_at:insert_at] = history
            content = run_agent_turn(
                router,
                messages=messages,
                account_id=account_id,
                user_dek=user_dek,
                session_id=session_id,
            )

            if isinstance(content, tuple):
                text, total = content
            else:
                text = content
                total = 0
            if text:
                return (text, total) if total else text
        except Exception as exc:
            logger.warning("Chat model failed (non-streaming), falling back: %s", exc)

    # Fallback when model is unavailable.
    if lesson_text or doc_block:
        return (
            f"I can still help in local mode. Lesson: {lesson_text or 'n/a'}. "
            f"Context: {(doc_block[:220] + '...') if doc_block else 'none'}. "
            f"You asked: {user_message.text}",
            0,
        )
    if _router_unavailable_reason:
        return f"Chat model unavailable ({_router_unavailable_reason}). You asked: {user_message.text}", 0
    return f"You asked: {user_message.text}", 0


def _complete_with_tools_stream(
    router: QuizIntentRouter,
    *,
    messages: list[dict[str, object]],
    account_id: str,
    user_dek: bytes | None,
):
    """Yields (event_type, data) tuples where event_type is 'token', 'tool', or 'done'.

    The first LLM call streams plain text immediately with a short holdback
    window to avoid leaking DSML/tool markup split across chunks.

    IMPORTANT: Raw model content is NEVER yielded to the caller. Only clean,
    DSML-free text reaches the frontend.
    """
    tools = _build_tool_spec(account_id)
    stream = router.client.chat.completions.create(
        model=router.model,
        messages=messages,
        tools=tools,
        tool_choice="auto",
        stream=True,
        timeout=CHAT_TOOL_TIMEOUT_SEC,
        **_thinking_kwargs(router),
    )

    raw_content = ""
    raw_reasoning = ""
    tool_call_accumulator: dict[int, dict] = {}
    emitted_plain_content = False
    holdback = ""
    dsml_mode = False
    dsml_marker_window = max(
        len(f"<{_DSML_FULLWIDTH_BARS}DSML{_DSML_FULLWIDTH_BARS}"),
        len(f"<{_DSML_MOJIBAKE_BARS}DSML{_DSML_MOJIBAKE_BARS}"),
    )

    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta is None:
            continue

        if delta.content:
            raw_content += delta.content
            if not tool_call_accumulator and not dsml_mode:
                holdback += delta.content
                if _HAS_DSML.search(holdback):
                    dsml_mode = True
                    emitted_plain_content = False
                    holdback = ""
                elif len(holdback) > dsml_marker_window:
                    safe_len = len(holdback) - dsml_marker_window
                    safe_text = holdback[:safe_len]
                    holdback = holdback[safe_len:]
                    if safe_text:
                        emitted_plain_content = True
                        yield ("token", safe_text)

        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            raw_reasoning += reasoning

        if delta.tool_calls:
            holdback = ""
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tool_call_accumulator:
                    tool_call_accumulator[idx] = {
                        "id": tc_delta.id or "",
                        "name": "",
                        "arguments": "",
                    }
                entry = tool_call_accumulator[idx]
                if tc_delta.id:
                    entry["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        entry["name"] = tc_delta.function.name
                    if tc_delta.function.arguments:
                        entry["arguments"] += tc_delta.function.arguments

    tool_calls = [
        {
            "id": entry["id"],
            "type": "function",
            "function": {
                "name": entry["name"],
                "arguments": entry["arguments"],
            },
        }
        for entry in tool_call_accumulator.values()
        if entry["name"]
    ]

    content = _strip_dsml(raw_content)

    dsml_calls = []
    if not tool_calls and raw_content and _HAS_DSML.search(raw_content):
        dsml_calls = _parse_dsml_tool_calls(raw_content)

    if not tool_calls and not dsml_calls:
        if raw_reasoning:
            yield ("thinking", raw_reasoning)
        if emitted_plain_content and holdback:
            yield ("token", holdback)
        elif not emitted_plain_content and content:
            yield ("token", content)
        yield ("done", "")
        return

    # --- Tool calls detected: yield thinking, execute tools, then stream followup ---
    if raw_reasoning:
        yield ("thinking", raw_reasoning)
    if content:
        yield ("token", content)

    active_calls = tool_calls or dsml_calls

    last_tool_payload = ""
    executed_calls: list[dict[str, object]] = []
    tool_results: list[dict[str, object]] = []
    for idx, tool_call in enumerate(active_calls):
        if idx >= MAX_TOOL_CALLS_PER_TURN:
            logger.warning("Tool call limit reached (%d), stopping loop", MAX_TOOL_CALLS_PER_TURN)
            break
        if isinstance(tool_call, dict):
            name = tool_call["function"]["name"]
            raw_arguments = tool_call["function"]["arguments"]
            call_id = tool_call["id"]
            executed_calls.append(tool_call)
        else:
            function = getattr(tool_call, "function", None)
            name = getattr(function, "name", "")
            raw_arguments = getattr(function, "arguments", "") or "{}"
            call_id = getattr(tool_call, "id", "")
            executed_calls.append({
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": raw_arguments},
            })

        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            arguments = {}
        try:
            last_tool_payload = _tool_result_payload(name, arguments, account_id, user_dek)
        except Exception as exc:
            last_tool_payload = json.dumps({"error": str(exc)}, ensure_ascii=True)

        yield ("tool", f"Executed tool: {name}")

        tool_results.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": last_tool_payload,
            }
        )

    followup_messages = list(messages)
    if executed_calls:
        assistant_msg: dict[str, object] = {
            "role": "assistant",
            "content": content,
            "tool_calls": executed_calls,
        }
        if raw_reasoning:
            assistant_msg["reasoning_content"] = raw_reasoning
        followup_messages.append(assistant_msg)
    followup_messages.extend(tool_results)

    # Buffer the followup so DSML tags are stripped before any content reaches the caller
    followup_stream = router.client.chat.completions.create(
        model=router.model,
        messages=followup_messages,
        stream=True,
        timeout=CHAT_TOOL_TIMEOUT_SEC,
        **_thinking_kwargs(router),
    )
    final_content = ""
    followup_holdback = ""
    followup_emitted = False
    followup_dsml_mode = False
    for chunk in followup_stream:
        if chunk.choices and chunk.choices[0].delta.content:
            chunk_text = chunk.choices[0].delta.content
            final_content += chunk_text
            if not followup_dsml_mode:
                followup_holdback += chunk_text
                if _HAS_DSML.search(followup_holdback):
                    followup_dsml_mode = True
                    followup_emitted = False
                    followup_holdback = ""
                elif len(followup_holdback) > dsml_marker_window:
                    safe_len = len(followup_holdback) - dsml_marker_window
                    safe_text = followup_holdback[:safe_len]
                    followup_holdback = followup_holdback[safe_len:]
                    if safe_text:
                        followup_emitted = True
                        yield ("token", safe_text)

    final_content = _strip_dsml(final_content.strip())
    if followup_emitted and followup_holdback:
        yield ("token", followup_holdback)
    elif not followup_emitted and final_content:
        yield ("token", final_content)

    yield ("done", "")


def generate_reply_stream(
    user_message: ChatMessage,
    lesson: Lesson | None,
    doc_texts: list[str],
    account_id: str = "admin",
    user_dek: bytes | None = None,
    past_messages: list[ChatMessage] | None = None,
    session_id: str | None = None,
    query_vector: list[float] | None = None,
):
    """Yields (event_type, data) tuples for streaming: 'token', 'tool', or 'done'.

    The first LLM call is streamed internally to collect tool-call deltas, but
    output is buffered until DSML/tool markup can be stripped safely.
    """
    router = get_router()
    system_prompt = get_effective_system_prompt(account_id=account_id, user_dek=user_dek)
    lesson_text = lesson.title if lesson else ""
    had_explicit_doc_context = bool(doc_texts)

    if not doc_texts:
        doc_texts = _search_relevant_chunks(user_message.text, account_id, user_dek, query_vector=query_vector)
        doc_block = "\n".join(doc_texts[:3])[:2500]
    else:
        doc_block = "\n".join(doc_texts[:10])[:8000]

    if doc_texts:
        yield (
            "chat_status",
            {
                "kind": "rag",
                "status": "done",
                "detail": (
                    f"Using {len(doc_texts)} document chunk"
                    f"{'' if len(doc_texts) == 1 else 's'} from "
                    f"{'selected context' if had_explicit_doc_context else 'semantic search'}."
                ),
            },
        )
    elif query_vector:
        yield (
            "chat_status",
            {
                "kind": "rag",
                "status": "done",
                "detail": "Semantic document search ran, but no matching chunks passed the threshold.",
            },
        )
    else:
        yield (
            "chat_status",
            {
                "kind": "rag",
                "status": "done",
                "detail": "Document retrieval skipped because no client query vector was provided.",
            },
        )

    if router is not None:
        try:
            from backend.app.services.memory_service import (
                build_dialectic_context,
                get_account_store,
                get_session_summary,
            )
            store = get_account_store()
            user_profile = store.get_user_profile(account_id)
            agent_profile = store.get_agent_profile(account_id)
            session_summary = get_session_summary(session_id or "") if session_id else None
            dialectic_block = build_dialectic_context(
                user_profile=user_profile,
                agent_profile=agent_profile,
                session_summary=session_summary,
            )

            messages = router.tutor_context_builder.build_messages(
                user_payload={
                    "student_ask": user_message.text,
                    "context": doc_block or lesson_text,
                }
            )
            messages = _apply_system_prompt(messages, system_prompt, dialectic_block=dialectic_block)
            # Insert conversation history after system message, before current user message
            if past_messages:
                history = []
                for m in past_messages[-5:]:
                    role = "assistant" if m.role == "assistant" else "user"
                    history.append({"role": role, "content": m.text})
                sys_idx = next((i for i, m in enumerate(messages) if m.get("role") == "system"), -1)
                insert_at = sys_idx + 1 if sys_idx >= 0 else 0
                messages[insert_at:insert_at] = history
            yield from run_agent_turn_stream(
                router,
                messages=messages,
                account_id=account_id,
                user_dek=user_dek,
                session_id=session_id,
            )
            return
        except Exception as exc:
            logger.warning("Chat model failed, falling back to local mode: %s", exc)

    # Fallback when model is unavailable.
    if lesson_text or doc_block:
        yield ("token", (
            f"I can still help in local mode. Lesson: {lesson_text or 'n/a'}. "
            f"Context: {(doc_block[:220] + '...') if doc_block else 'none'}. "
            f"You asked: {user_message.text}"
        ))
        yield ("done", "")
        return
    if _router_unavailable_reason:
        yield ("token", f"Chat model unavailable ({_router_unavailable_reason}). You asked: {user_message.text}")
        yield ("done", "")
        return
    yield ("token", f"You asked: {user_message.text}")
    yield ("done", "")


def extract_doc_texts(docs: list[Doc], chunk_ids: list[str]) -> list[str]:
    wanted = set(chunk_ids)
    found: list[str] = []
    for doc in docs:
        for chunk in doc.chunks:
            if chunk.chunk_id in wanted:
                found.append(chunk.text)
    return found
