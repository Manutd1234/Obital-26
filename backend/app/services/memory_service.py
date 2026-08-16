from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path

from engine.quiz.account_system import AccountStore

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parents[3]
_PROFILE_AUDIT_INTERVAL = int(os.getenv("OBITAL_PROFILE_AUDIT_INTERVAL", "5"))
_AUDIT_MODEL = os.getenv("OBITAL_PROFILE_AUDIT_MODEL", "deepseek-chat")
_MAX_PROFILE_CHARS = 8000
_MIN_MESSAGE_LEN_FOR_AUDIT = 20

_memory_lock = threading.Lock()
_session_summaries: dict[str, str] = {}
_audit_counts: dict[str, int] = {}

_STORE: AccountStore | None = None


def get_account_store() -> AccountStore:
    global _STORE
    if _STORE is None:
        _STORE = AccountStore(_BASE_DIR / "engine" / "accounts" / "accounts.json")
    return _STORE


def build_dialectic_context(
    *,
    user_profile: str,
    agent_profile: str,
    session_summary: str | None,
) -> str:
    parts = []
    parts.append("<memory-context>")
    if user_profile.strip():
        parts.append(f"### Who is this user\n{user_profile.strip()}")
    if agent_profile.strip():
        parts.append(f"### How to communicate with this user\n{agent_profile.strip()}")
    if not user_profile.strip() and not agent_profile.strip():
        parts.append("No long-term memory yet — new or unrecognized user.")
    parts.append("</memory-context>")
    parts.append("")
    parts.append("<dialectic-supplement>")
    parts.append((session_summary or "No session summary yet — new session.").strip())
    parts.append("</dialectic-supplement>")
    return "\n".join(parts) + "\n"


def get_session_summary(session_id: str) -> str | None:
    return _session_summaries.get(session_id)


def update_session_summary(
    *,
    session_id: str,
    old_summary: str | None,
    new_messages_text: str,
    client,
) -> None:
    if not new_messages_text.strip():
        return

    if old_summary:
        prompt = (
            "You are updating a rolling session summary.\n\n"
            f"=== EXISTING SUMMARY ===\n{old_summary}\n\n"
            f"=== NEW MESSAGES ===\n{new_messages_text}\n\n"
            "Update the summary to incorporate the new messages. Keep it under 300 words. "
            "Focus on: what the user is trying to learn, topics covered, "
            "what questions remain open."
        )
    else:
        prompt = (
            "Summarize this conversation so far. Keep it under 300 words. "
            "Focus on: what the user is trying to learn, topics covered, "
            "what questions remain open.\n\n"
            f"=== CONVERSATION ===\n{new_messages_text}"
        )

    try:
        response = client.chat.completions.create(
            model=_AUDIT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=30,
        )
        summary = (response.choices[0].message.content or "").strip()
        if len(summary) > 3000:
            summary = summary[:3000]
        _session_summaries[session_id] = summary
    except Exception as exc:
        logger.warning("Session summary update failed for %s: %s", session_id, exc)


def maybe_audit_user_profile(
    *,
    account_id: str,
    transcript: str,
    client,
) -> None:
    if len(transcript.strip()) < _MIN_MESSAGE_LEN_FOR_AUDIT:
        return

    with _memory_lock:
        count = _audit_counts.get(account_id, 0) + 1
        _audit_counts[account_id] = count

    if count < _PROFILE_AUDIT_INTERVAL:
        return

    with _memory_lock:
        _audit_counts[account_id] = 0

    store = get_account_store()
    user_profile = store.get_user_profile(account_id)
    agent_profile = store.get_agent_profile(account_id)

    thread = threading.Thread(
        target=_safe_run_profile_audit,
        args=(account_id, user_profile, agent_profile, transcript, client),
        daemon=True,
    )
    thread.start()


def _safe_run_profile_audit(
    account_id: str,
    user_profile: str,
    agent_profile: str,
    transcript: str,
    client,
) -> None:
    try:
        _run_profile_audit(
            account_id=account_id,
            user_profile=user_profile,
            agent_profile=agent_profile,
            transcript=transcript,
            client=client,
        )
    except Exception as exc:
        logger.warning("Profile audit failed silently for %s: %s", account_id, exc)


_AUDIT_PROMPT = """\
You are a memory janitor. Review the conversation transcript below.
Compare it against the existing profiles.

[EXISTING USER PROFILE]
{user_profile}

[EXISTING AGENT PROFILE]
{agent_profile}

[NEW CONVERSATION TRANSCRIPT]
{transcript}

Tasks:
1. Identify any new permanent details the user mentioned: personal facts,
   habits, code preferences, tech stacks, tools they use, projects they work
   on, interests, pets, location, or life context. Update the User Profile.
   Streamline to under 1,000 words. Remove outdated or contradictory info.
2. Identify any preferences for how the assistant should communicate with
   this user (tone, format, depth, style). Update the Agent Profile.
   Streamline to under 500 words. Remove outdated or contradictory info.

Do NOT add fleeting context (like bugs fixed or syntax errors in one chat
turn). BUT do record durable personal facts like pets, family, hobbies,
home setup, or work context when disclosed.

Return EXACTLY this format with NO preamble or commentary:

===USER_PROFILE===
[updated user profile markdown]

===AGENT_PROFILE===
[updated agent profile markdown]"""


_PROFILE_SECTION_RE = re.compile(
    r"===USER_PROFILE===\s*(.*?)\s*===AGENT_PROFILE===\s*(.*?)$",
    re.DOTALL,
)

_FENCE_RE = re.compile(r"^```\w*\n?|\n?```$", re.MULTILINE)


def _extract_section(raw: str, section_name: str) -> str:
    match = _PROFILE_SECTION_RE.search(raw)
    if not match:
        return ""
    text = match.group(1 if section_name == "user" else 2)
    text = text.strip()
    text = _FENCE_RE.sub("", text)
    return text.strip()


def _run_profile_audit(
    *,
    account_id: str,
    user_profile: str,
    agent_profile: str,
    transcript: str,
    client,
) -> None:
    prompt = _AUDIT_PROMPT.format(
        user_profile=user_profile or "(empty)",
        agent_profile=agent_profile or "(empty)",
        transcript=transcript[-16000:],
    )

    response = client.chat.completions.create(
        model=_AUDIT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        timeout=60,
    )
    raw = (response.choices[0].message.content or "").strip()

    new_user_profile = _truncate_profile(_extract_section(raw, "user"))
    new_agent_profile = _truncate_profile(_extract_section(raw, "agent"), max_chars=4000)

    if not new_user_profile and not new_agent_profile:
        logger.warning("Profile audit: couldn't parse delimited output for %s", account_id)
        return

    store = get_account_store()
    if new_user_profile:
        store.update_user_profile(account_id, new_user_profile)
    if new_agent_profile:
        store.update_agent_profile(account_id, new_agent_profile)
    logger.info(
        "Profile audit complete for %s (user=%d chars, agent=%d chars)",
        account_id, len(new_user_profile), len(new_agent_profile),
    )


def _truncate_profile(text: str, max_chars: int = _MAX_PROFILE_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    for sep in (". ", "! ", "? "):
        last = truncated.rfind(sep)
        if last > max_chars * 0.5:
            return truncated[:last + 1].strip()
    return truncated.strip()
