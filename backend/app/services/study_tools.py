# Authored by Noah — https://github.com/noah-e1528170
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Literal

from backend.app.schemas import ChatSettingsPatch, SettingsPatchRequest
from backend.app.services.chat_tools import (
    create_lesson_payload,
    exit_quiz_payload,
    review_quiz_payload,
    submit_answer_payload,
    submit_quiz_payload,
)
from backend.app.services.settings_service import get_settings, update_settings
from backend.app.services.storage import get_rewards_dashboard, list_account_lessons, list_docs, list_lessons

ToolSafety = Literal["read", "mutation"]

BASE_DIR = Path(__file__).resolve().parents[3]
_QA_ARTIFACT_PATH = "output/output_curriculum_qa.json"


@dataclass(frozen=True)
class StudyTool:
    name: str
    safety: ToolSafety
    label: str
    schema: dict
    executor: Callable[[dict[str, object], str, bytes | None], str]

    def openai_spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.schema["description"],
                "parameters": self.schema["parameters"],
            },
        }


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=True)


def _get_system_prompt(_: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    from backend.app.services import chat_engine

    current = chat_engine.get_settings(account_id=account_id, user_dek=user_dek).chat.system_prompt
    return _json({"system_prompt": current})


def _update_system_prompt(arguments: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    from backend.app.services import chat_engine

    raw_prompt = arguments.get("system_prompt", "")
    if not isinstance(raw_prompt, str):
        raise ValueError("system_prompt must be a string")
    updated = chat_engine.update_settings(
        account_id=account_id,
        patch=SettingsPatchRequest(chat=ChatSettingsPatch(system_prompt=raw_prompt)),
        user_dek=user_dek,
    ).chat.system_prompt
    return _json({"status": "updated", "system_prompt": updated})


def _get_dashboard_summary(_: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    data = get_rewards_dashboard(account_id=account_id, limit=5, user_dek=user_dek)
    summary = data.get("summary", {})
    return _json({
        "total_points": summary.get("total_points", 0),
        "total_quizzes": summary.get("total_quizzes", 0),
        "accuracy_percent": summary.get("accuracy_percent", 0),
        "total_correct": summary.get("total_correct", 0),
        "total_answered": summary.get("total_answered", 0),
    })


def _list_lessons(_: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    all_lessons = list_account_lessons(account_id=account_id, user_dek=user_dek)
    items = []
    for i, lesson in enumerate(all_lessons, 1):
        items.append({
            "index": i,
            "id": lesson.id,
            "title": lesson.title,
            "objective": lesson.objective,
            "status": lesson.status,
            "current_checkpoint": lesson.current_checkpoint,
            "total_checkpoints": lesson.total_checkpoints,
        })
    return _json({"lessons": items})


def _list_docs(_: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    docs = list_docs(account_id=account_id, user_dek=user_dek)
    return _json({
        "documents": [
            {"index": i, "name": doc.name, "chunk_count": len(doc.chunks) if doc.chunks else 0}
            for i, doc in enumerate(docs, 1)
        ]
    })


def _open_doc_excerpt(arguments: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    doc_name = arguments.get("doc_name")
    docs = list_docs(account_id=account_id, user_dek=user_dek)
    doc = None
    if doc_name:
        for candidate in docs:
            if candidate.name.lower() == str(doc_name).lower():
                doc = candidate
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
    text = "\n\n".join(chunk.text for chunk in (doc.chunks or [])[:5])
    return _json({"name": doc.name, "excerpt": text[:3000], "chunk_count": len(doc.chunks or [])})


def _create_lesson(arguments: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    topic = str(arguments.get("topic", "")).strip()
    if not topic:
        raise ValueError("topic is required")
    title = str(arguments.get("title", "")).strip() or None
    return _json(create_lesson_payload(topic=topic, title=title, account_id=account_id, use_harness=True, user_dek=user_dek))


def _search_wikipedia(arguments: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    from backend.app.services.web_search import search_wikipedia

    query = str(arguments.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")
    return _json(search_wikipedia(query))


def _search_web(arguments: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    from backend.app.services.web_search import search_web_and_wikipedia

    query = str(arguments.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")
    try:
        num_results = int(arguments.get("num_results", 5))
    except (TypeError, ValueError):
        num_results = 5
    return _json(search_web_and_wikipedia(query, num_results=num_results))


def _submit_answer(arguments: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    answer = str(arguments.get("answer", "")).strip()
    if not answer:
        raise ValueError("answer is required")
    return _json(submit_answer_payload(answer=answer, account_id=account_id, user_dek=user_dek))


def _review_quiz(_: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    return _json(review_quiz_payload(account_id=account_id))


def _exit_quiz(_: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    return _json(exit_quiz_payload(account_id=account_id))


def _submit_quiz(_: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    return _json(submit_quiz_payload(account_id=account_id, user_dek=user_dek))


def _save_generated_qa(arguments: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    curriculum_qa = arguments.get("curriculum_qa")
    if not isinstance(curriculum_qa, dict):
        raise ValueError("curriculum_qa must be an object")
    content_bytes = json.dumps(curriculum_qa).encode("utf-8")
    if _is_stateless_db_mode():
        from backend.app.services.crypto_local import encrypt_bytes, require_dek
        from backend.app.services.db import SessionLocal
        from backend.app.services.models import AccountArtifactModel

        assert SessionLocal is not None
        stored = encrypt_bytes(require_dek(user_dek, "pipeline artifact"), content_bytes)
        sha256_hash = hashlib.sha256(content_bytes).hexdigest()
        with SessionLocal() as db:
            exists = db.query(AccountArtifactModel).where(
                AccountArtifactModel.account_id == account_id,
                AccountArtifactModel.relative_path == _QA_ARTIFACT_PATH,
            ).first()
            if exists:
                exists.content = stored
                exists.sha256 = sha256_hash
                exists.size_bytes = len(stored)
                exists.updated_at = datetime.now(UTC)
            else:
                db.add(AccountArtifactModel(
                    account_id=account_id,
                    relative_path=_QA_ARTIFACT_PATH,
                    content=stored,
                    sha256=sha256_hash,
                    size_bytes=len(stored),
                    updated_at=datetime.now(UTC),
                ))
            db.commit()
    else:
        from engine.shared.account_paths import account_qa_output_path

        output_path = account_qa_output_path(base_dir=BASE_DIR, account_id=account_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(curriculum_qa, indent=2), encoding="utf-8")
    return _json({"status": "ok", "saved_path": _QA_ARTIFACT_PATH})


def _start_qa_generation_job(arguments: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    from backend.app.services.generation_jobs import create_qa_generation_job

    return _json(create_qa_generation_job(account_id=account_id, user_dek=user_dek))


def _is_stateless_db_mode() -> bool:
    from backend.app.services.db import is_stateless_db_mode

    return is_stateless_db_mode()


TOOLS: dict[str, StudyTool] = {
    "get_system_prompt": StudyTool("get_system_prompt", "read", "Reading tutor prompt", {
        "description": "Get current system prompt.",
        "parameters": {"type": "object", "properties": {}},
    }, _get_system_prompt),
    "update_system_prompt": StudyTool("update_system_prompt", "mutation", "Update tutor prompt", {
        "description": "Update system prompt.",
        "parameters": {
            "type": "object",
            "properties": {"system_prompt": {"type": "string"}},
            "required": ["system_prompt"],
        },
    }, _update_system_prompt),
    "get_dashboard_summary": StudyTool("get_dashboard_summary", "read", "Reading dashboard", {
        "description": "Get dashboard: points, quizzes, accuracy.",
        "parameters": {"type": "object", "properties": {}},
    }, _get_dashboard_summary),
    "list_lessons": StudyTool("list_lessons", "read", "Listing lessons", {
        "description": "List lessons with progress.",
        "parameters": {"type": "object", "properties": {}},
    }, _list_lessons),
    "list_docs": StudyTool("list_docs", "read", "Listing documents", {
        "description": "List uploaded documents.",
        "parameters": {"type": "object", "properties": {}},
    }, _list_docs),
    "open_doc_excerpt": StudyTool("open_doc_excerpt", "read", "Opening document excerpt", {
        "description": "Get text excerpt from a document.",
        "parameters": {
            "type": "object",
            "properties": {"doc_index": {"type": "integer"}, "doc_name": {"type": "string"}},
        },
    }, _open_doc_excerpt),
    "create_lesson": StudyTool("create_lesson", "mutation", "Create lesson", {
        "description": "Start a lesson on a topic.",
        "parameters": {
            "type": "object",
            "properties": {"topic": {"type": "string"}, "title": {"type": "string"}},
            "required": ["topic"],
        },
    }, _create_lesson),
    "save_generated_qa": StudyTool("save_generated_qa", "mutation", "Save generated QA", {
        "description": "Save a generated curriculum QA artifact for future quizzes.",
        "parameters": {
            "type": "object",
            "properties": {"curriculum_qa": {"type": "object"}},
            "required": ["curriculum_qa"],
        },
    }, _save_generated_qa),
    "start_qa_generation_job": StudyTool("start_qa_generation_job", "mutation", "Generate quiz and lesson material", {
        "description": "Start a background job that generates curriculum and QA material from uploaded documents.",
        "parameters": {"type": "object", "properties": {}},
    }, _start_qa_generation_job),
    "search_wikipedia": StudyTool("search_wikipedia", "read", "Searching Wikipedia", {
        "description": "Search Wikipedia only. For current events, latest information, or broad open-web questions, prefer search_web.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }, _search_wikipedia),
    "search_web": StudyTool("search_web", "read", "Searching web", {
        "description": "Search the live web with Exa and Wikipedia in parallel. Use this for current events, latest news, recent facts, or external knowledge not in the student's documents. Web results are prioritized over Wikipedia.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "num_results": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["query"],
        },
    }, _search_web),
    "submit_answer": StudyTool("submit_answer", "mutation", "Submit quiz answer", {
        "description": "Submit quiz answer. Use when student gives an answer.",
        "parameters": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    }, _submit_answer),
    "review_quiz": StudyTool("review_quiz", "read", "Review quiz", {
        "description": "Show quiz progress.",
        "parameters": {"type": "object", "properties": {}},
    }, _review_quiz),
    "exit_quiz": StudyTool("exit_quiz", "mutation", "Exit quiz", {
        "description": "End quiz early.",
        "parameters": {"type": "object", "properties": {}},
    }, _exit_quiz),
    "submit_quiz": StudyTool("submit_quiz", "mutation", "Submit quiz", {
        "description": "Grade and finalize quiz.",
        "parameters": {"type": "object", "properties": {}},
    }, _submit_quiz),
}


def build_tool_spec(account_id: str) -> list[dict]:
    names = [
        "get_system_prompt",
        "update_system_prompt",
        "get_dashboard_summary",
        "list_lessons",
        "list_docs",
        "open_doc_excerpt",
        "create_lesson",
        "save_generated_qa",
        "start_qa_generation_job",
        "search_web",
        "search_wikipedia",
    ]
    from engine.quiz.account_system import AccountStore

    store = AccountStore(BASE_DIR / "engine" / "accounts" / "accounts.json")
    if store.get_active_quiz(username=account_id) is not None:
        names.extend(["submit_answer", "review_quiz", "exit_quiz", "submit_quiz"])
    return [TOOLS[name].openai_spec() for name in names]


def execute_tool(name: str, arguments: dict[str, object], account_id: str, user_dek: bytes | None) -> str:
    tool = TOOLS.get(name)
    if tool is None:
        raise ValueError(f"Unknown tool '{name}'")
    return tool.executor(arguments, account_id, user_dek)


def get_tool(name: str) -> StudyTool | None:
    return TOOLS.get(name)
