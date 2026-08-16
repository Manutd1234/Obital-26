from __future__ import annotations

import itertools
import json
import logging
import re
import uuid
from typing import Iterator

from backend.app.services.harness_state import put_pending_action
from backend.app.services.study_tools import build_tool_spec, execute_tool, get_tool
from backend.app.services.web_search import SearchBudget, provider_request_count, search_limit_payload

logger = logging.getLogger(__name__)

CHAT_TOOL_TIMEOUT_SEC = 12.0
MAX_TOOL_CALLS_PER_TURN = 5
MAX_AGENT_ROUNDS = 3
MAX_COMPLEX_AGENT_ROUNDS = 5

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
_dsml_call_counter = itertools.count()


def strip_dsml(text: str) -> str:
    if not text:
        return ""
    if _HAS_DSML.search(text):
        cleaned = re.sub(
            rf"<{_DSML_PREFIX_RE}tool_calls\s*>.*?</{_DSML_PREFIX_RE}tool_calls\s*>",
            "",
            text,
            flags=re.DOTALL,
        )
        cleaned = re.sub(
            rf"<{_DSML_PREFIX_RE}invoke\s+name=\"[^\"]+\"\s*>.*?</{_DSML_PREFIX_RE}invoke\s*>",
            "",
            cleaned,
            flags=re.DOTALL,
        )
        cleaned = _DSML_RE.sub("", cleaned).strip()
        cleaned = re.sub(r'<ï½œï½œDSMLï½œï½œ[^>]*', "", cleaned)
        cleaned = re.sub(r'</ï½œï½œDSMLï½œï½œ[^>]*', "", cleaned)
        return cleaned
    return text


def parse_dsml_tool_calls(text: str) -> list[dict[str, object]]:
    tool_calls = []
    for match in _DSML_INVOKE_RE.finditer(text):
        name = match.group(1)
        body = match.group(2)
        arguments = {}
        for p_match in _DSML_PARAM_RE.finditer(body):
            arguments[p_match.group(1)] = p_match.group(2).strip()
        tool_calls.append({
            "id": f"call_dsml_{next(_dsml_call_counter)}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=True)},
        })
    return tool_calls


def _thinking_kwargs(router) -> dict:
    if getattr(router, "thinking_enabled", False):
        return {
            "reasoning_effort": getattr(router, "reasoning_effort", "high"),
            "extra_body": {"thinking": {"type": "enabled"}},
        }
    return {}


def _normalize_tool_call(tool_call) -> tuple[str, str, str, dict[str, object]]:
    if isinstance(tool_call, dict):
        name = str(tool_call["function"]["name"])
        raw_arguments = str(tool_call["function"].get("arguments") or "{}")
        call_id = str(tool_call.get("id") or "")
        executed = tool_call
    else:
        function = getattr(tool_call, "function", None)
        name = str(getattr(function, "name", "") or "")
        raw_arguments = str(getattr(function, "arguments", "") or "{}")
        call_id = str(getattr(tool_call, "id", "") or "")
        executed = {"id": call_id, "type": "function", "function": {"name": name, "arguments": raw_arguments}}
    try:
        arguments = json.loads(raw_arguments)
        if not isinstance(arguments, dict):
            arguments = {}
    except json.JSONDecodeError:
        arguments = {}
    return name, call_id, raw_arguments, {"executed": executed, "arguments": arguments}


def _preview_payload(payload: str, *, limit: int = 1200) -> str:
    compact = payload.strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def _fallback_tool_payload_text(tool_results: list[dict[str, object]]) -> str:
    """Return a readable answer when a post-tool model follow-up is empty."""
    if not tool_results:
        return ""
    last_result = tool_results[-1]
    name = str(last_result.get("name") or "")
    content = str(last_result.get("content") or "").strip()
    if not content:
        return ""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content
    if not isinstance(payload, dict):
        return content

    if name == "open_doc_excerpt":
        doc_name = str(payload.get("name") or "document")
        excerpt = str(payload.get("excerpt") or "").strip()
        if excerpt:
            return f"Here is what I found in `{doc_name}`:\n\n{excerpt}"
        return f"I opened `{doc_name}`, but it did not contain readable text."

    if name == "list_docs":
        documents = payload.get("documents")
        if isinstance(documents, list):
            if not documents:
                return "No documents are uploaded yet."
            lines = ["Here are your uploaded documents:"]
            for item in documents:
                if not isinstance(item, dict):
                    continue
                index = item.get("index", "")
                doc_name = item.get("name", "Untitled")
                chunks = item.get("chunk_count", 0)
                lines.append(f"{index}. {doc_name} ({chunks} chunks)")
            return "\n".join(lines)

    if "error" in payload:
        return f"Tool error: {payload['error']}"
    return content


def _tool_answer_instruction() -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            "Use the tool result above to answer my original request directly. "
            "If the tool returned document text, include the relevant contents. "
            "Do not call more tools."
        ),
    }


def _max_rounds_for_messages(messages: list[dict[str, object]]) -> int:
    text = "\n".join(str(message.get("content", "")) for message in messages[-4:])
    lowered = text.lower()
    if any(token in lowered for token in ("lesson", "quiz", "debug", "compare", "analyze", "generate", "build")):
        return MAX_COMPLEX_AGENT_ROUNDS
    return MAX_AGENT_ROUNDS


def _tool_payload_or_confirmation(
    *,
    name: str,
    arguments: dict[str, object],
    account_id: str,
    user_dek: bytes | None,
    session_id: str | None,
    search_budget: SearchBudget | None = None,
) -> tuple[str, str, dict[str, object] | None]:
    tool = get_tool(name)
    if tool is None:
        raise ValueError(f"Unknown tool '{name}'")
    requested_searches = provider_request_count(name)
    if requested_searches and search_budget is not None:
        next_budget, allowed = search_budget.consume(requested_searches)
        if allowed < requested_searches:
            return (
                json.dumps(
                    search_limit_payload(requested=requested_searches, remaining=search_budget.remaining),
                    ensure_ascii=True,
                ),
                "tool_done",
                {"search_budget": next_budget},
            )
        search_budget = next_budget
    if tool.safety == "mutation":
        if not session_id:
            raise ValueError("Mutation tools require a chat session for confirmation")
        action = put_pending_action(
            session_id=session_id,
            account_id=account_id,
            tool_name=name,
            arguments=arguments,
            label=tool.label,
        )
        payload = {
            "confirmation_required": True,
            "action_id": action["action_id"],
            "tool_name": name,
            "label": tool.label,
            "arguments": arguments,
        }
        return json.dumps(payload, ensure_ascii=True), "confirmation_required", payload
    return execute_tool(name, arguments, account_id, user_dek), "tool_done", (
        {"search_budget": search_budget} if requested_searches and search_budget is not None else None
    )


def _response_token_usage(response) -> int:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    total = getattr(usage, "total_tokens", None)
    if total is not None:
        try:
            return int(total)
        except (TypeError, ValueError):
            return 0
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    try:
        return int(prompt) + int(completion)
    except (TypeError, ValueError):
        return 0


def run_agent_turn(
    router,
    *,
    messages: list[dict[str, object]],
    account_id: str,
    user_dek: bytes | None,
    session_id: str | None = None,
) -> tuple[str, int]:
    turn_id = uuid.uuid4().hex[:12]
    round_messages = list(messages)
    max_rounds = _max_rounds_for_messages(messages)
    last_tool_payload = ""
    last_content = ""
    total_tokens = 0
    search_budget = SearchBudget()

    for round_index in range(max_rounds):
        logger.info("agent_turn round start turn_id=%s round=%s max_rounds=%s", turn_id, round_index + 1, max_rounds)
        response = router.client.chat.completions.create(
            model=router.model,
            messages=round_messages,
            tools=build_tool_spec(account_id),
            tool_choice="auto",
            stream=False,
            timeout=CHAT_TOOL_TIMEOUT_SEC,
            **_thinking_kwargs(router),
        )
        total_tokens += _response_token_usage(response)
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        content = (getattr(message, "content", None) or "").strip()
        last_content = strip_dsml(content) or last_content
        dsml_calls = []
        if not tool_calls and _HAS_DSML.search(content):
            dsml_calls = parse_dsml_tool_calls(content)
        if not tool_calls and not dsml_calls:
            logger.info("agent_turn complete turn_id=%s round=%s stop=no_tools", turn_id, round_index + 1)
            return strip_dsml(content), total_tokens

        active_calls = tool_calls or dsml_calls
        executed_calls: list[dict[str, object]] = []
        tool_results: list[dict[str, object]] = []
        for idx, tool_call in enumerate(active_calls):
            if idx >= MAX_TOOL_CALLS_PER_TURN:
                logger.warning("Tool call limit reached (%d), stopping loop", MAX_TOOL_CALLS_PER_TURN)
                break
            name, call_id, _raw, normalized = _normalize_tool_call(tool_call)
            executed_calls.append(normalized["executed"])
            logger.info("agent_turn tool turn_id=%s round=%s tool=%s", turn_id, round_index + 1, name)
            try:
                last_tool_payload, event_type, confirmation = _tool_payload_or_confirmation(
                    name=name,
                    arguments=normalized["arguments"],
                    account_id=account_id,
                    user_dek=user_dek,
                    session_id=session_id,
                    search_budget=search_budget,
                )
                if confirmation and isinstance(confirmation.get("search_budget"), SearchBudget):
                    search_budget = confirmation["search_budget"]
                if event_type == "confirmation_required":
                    logger.info("agent_turn confirmation turn_id=%s tool=%s", turn_id, name)
                    return (
                        f"Please confirm: {confirmation['label']}.\n\n"
                        f"Action ID: `{confirmation['action_id']}`",
                        total_tokens,
                    )
            except Exception as exc:
                last_tool_payload = json.dumps({"error": str(exc)}, ensure_ascii=True)
            tool_results.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": last_tool_payload})

        if executed_calls:
            assistant_msg: dict[str, object] = {"role": "assistant", "content": content, "tool_calls": executed_calls}
            reasoning = getattr(message, "reasoning_content", None)
            if reasoning:
                assistant_msg["reasoning_content"] = reasoning
            round_messages.append(assistant_msg)
        round_messages.extend(tool_results)

    logger.info("agent_turn complete turn_id=%s stop=max_rounds", turn_id)
    followup = router.client.chat.completions.create(
        model=router.model,
        messages=round_messages + [
            {
                "role": "user",
                "content": "Summarize what you completed and the next best action. Do not call more tools.",
            }
        ],
        stream=False,
        timeout=CHAT_TOOL_TIMEOUT_SEC,
        **_thinking_kwargs(router),
    )
    total_tokens += _response_token_usage(followup)
    final_content = strip_dsml((followup.choices[0].message.content or "").strip())
    return final_content or last_content or last_tool_payload, total_tokens


def run_agent_turn_stream(
    router,
    *,
    messages: list[dict[str, object]],
    account_id: str,
    user_dek: bytes | None,
    session_id: str | None = None,
) -> Iterator[tuple[str, object]]:
    turn_id = uuid.uuid4().hex[:12]
    logger.info("agent_turn_stream start turn_id=%s session_id=%s", turn_id, session_id)
    yield ("thinking", {"delta": "Preparing the model request...\n", "status": "running"})
    tools = build_tool_spec(account_id)
    yield ("chat_status", {"kind": "model", "status": "running", "detail": "Waiting for model stream"})
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
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            raw_reasoning += reasoning
            yield ("thinking", {"delta": reasoning, "status": "running"})
        if delta.tool_calls:
            holdback = ""
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tool_call_accumulator:
                    tool_call_accumulator[idx] = {"id": tc_delta.id or "", "name": "", "arguments": ""}
                entry = tool_call_accumulator[idx]
                if tc_delta.id:
                    entry["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        entry["name"] = tc_delta.function.name
                    if tc_delta.function.arguments:
                        entry["arguments"] += tc_delta.function.arguments
    if not raw_reasoning:
        yield ("thinking", {"delta": "Model stream received. No provider reasoning trace was returned.\n", "status": "running"})

    tool_calls = [
        {"id": entry["id"], "type": "function", "function": {"name": entry["name"], "arguments": entry["arguments"]}}
        for entry in tool_call_accumulator.values()
        if entry["name"]
    ]
    content = strip_dsml(raw_content)
    dsml_calls = []
    if not tool_calls and raw_content and _HAS_DSML.search(raw_content):
        dsml_calls = parse_dsml_tool_calls(raw_content)
        if dsml_calls:
            content = ""
    if not tool_calls and not dsml_calls:
        yield ("thinking", {"status": "done"})
        if content:
            yield ("token", content)
        yield ("done", "")
        return

    yield ("thinking", {"status": "done"})

    executed_calls: list[dict[str, object]] = []
    tool_results: list[dict[str, object]] = []
    last_tool_payload = ""
    search_budget = SearchBudget()
    active_calls = tool_calls or dsml_calls
    for idx, tool_call in enumerate(active_calls):
        if idx >= MAX_TOOL_CALLS_PER_TURN:
            logger.warning("Tool call limit reached (%d), stopping loop", MAX_TOOL_CALLS_PER_TURN)
            break
        name, call_id, _raw, normalized = _normalize_tool_call(tool_call)
        executed_calls.append(normalized["executed"])
        tool = get_tool(name)
        yield (
            "tool_start",
            {
                "name": name,
                "label": tool.label if tool else name,
                "arguments": normalized["arguments"],
            },
        )
        try:
            last_tool_payload, event_type, confirmation = _tool_payload_or_confirmation(
                name=name,
                arguments=normalized["arguments"],
                account_id=account_id,
                user_dek=user_dek,
                session_id=session_id,
                search_budget=search_budget,
            )
            if confirmation and isinstance(confirmation.get("search_budget"), SearchBudget):
                search_budget = confirmation["search_budget"]
            if event_type == "confirmation_required":
                yield ("confirmation_required", confirmation)
                yield ("token", f"Please confirm: {confirmation['label']}.")
                yield ("done", "")
                return
        except Exception as exc:
            last_tool_payload = json.dumps({"error": str(exc)}, ensure_ascii=True)
        yield (
            "tool_done",
            {
                "name": name,
                "label": tool.label if tool else name,
                "status": "error" if '"error"' in last_tool_payload[:32] else "done",
                "result_preview": _preview_payload(last_tool_payload),
            },
        )
        yield ("tool", f"Executed tool: {name}")
        tool_results.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": last_tool_payload})

    followup_messages = list(messages)
    if executed_calls:
        assistant_msg: dict[str, object] = {"role": "assistant", "content": content, "tool_calls": executed_calls}
        if raw_reasoning:
            assistant_msg["reasoning_content"] = raw_reasoning
        followup_messages.append(assistant_msg)
    followup_messages.extend(tool_results)
    followup_messages.append(_tool_answer_instruction())
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
    final_content = strip_dsml(final_content.strip())
    if followup_emitted and followup_holdback:
        yield ("token", followup_holdback)
    elif not followup_emitted and final_content:
        yield ("token", final_content)
    elif not followup_emitted:
        fallback = _fallback_tool_payload_text(tool_results)
        if fallback:
            yield ("token", fallback)
    yield ("done", "")
