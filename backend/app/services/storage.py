"""Account-scoped document, chat, reward, and lesson persistence.

The service deliberately keeps filesystem and database paths behind the same
helpers so local SQLite mode and encrypted cloud mode share one contract.
"""

from __future__ import annotations

import functools
import json
import logging
import uuid
import re
from difflib import SequenceMatcher
import hashlib
from datetime import datetime, timedelta, UTC
from pathlib import Path
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import load_only, selectinload

from engine.shared.account_paths import (
    account_documents_dir,
    account_output_dir,
    account_qa_output_path,
)
from engine.quiz.account_system import AccountDataKeyMismatch, AccountStore

from backend.app.schemas import ChatMessage, ChatSession, Doc, DocChunk, DocEmbeddingMetadata, Lesson, LessonCheckpoint
from backend.app.services.crypto_local import decrypt_text, encrypt_text, decrypt_bytes, encrypt_bytes, require_dek
from backend.app.services.db import SessionLocal, db_enabled, is_stateless_db_mode, is_db_mode, is_sqlite_mode
from backend.app.services.models import (
    ChatMessageModel, ChatSessionModel, RewardEventModel, RewardSessionModel,
    AccountArtifactModel, AccountRegistryRecordModel, BinItemModel,
)

BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "backend" / "data"
CHAT_DIR = DATA_DIR / "chat"
SESSIONS_PATH = CHAT_DIR / "sessions.json"
REWARDS_DIR = DATA_DIR / "rewards"
logger = logging.getLogger(__name__)


def _is_db_storage_mode() -> bool:
    # SQLite is still a database mode: selecting the backend by configuration
    # keeps callers independent from whether the configured database is local
    # or remote.
    return is_db_mode()


def _session_local():
    if SessionLocal is None:
        raise RuntimeError("Database mode is enabled but SessionLocal is unavailable.")
    return SessionLocal


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    REWARDS_DIR.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _chunk_text(doc_id: str, text: str) -> list[DocChunk]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return [DocChunk(chunk_id=f"{doc_id}_chunk_1", text="(empty document)")]
    return [DocChunk(chunk_id=f"{doc_id}_chunk_{idx}", text=line) for idx, line in enumerate(lines, start=1)]


def _count_chunks(text: str) -> int:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 1
    return len(lines)


def _safe_doc_id(prefix: str, stem: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in stem.lower())
    return f"{prefix}_{cleaned[:48]}" if cleaned else f"{prefix}_{uuid.uuid4().hex[:8]}"


def _doc_id_for_path(prefix: str, path: Path) -> str:
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"{_safe_doc_id(prefix, path.stem)}_{digest}"


def _extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace")
    try:
        from engine.pipeline.extract_text import extract_text_from_path
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing optional dependency for non-text extraction. Use the project .venv with backend requirements."
        ) from exc
    return extract_text_from_path(path)


def bootstrap_data() -> None:
    _ensure_dirs()
    if not SESSIONS_PATH.exists():
        _write_json(SESSIONS_PATH, [])


def _load_curriculum_qa(account_id: str, user_dek: bytes | None = None) -> dict | None:
    if _is_db_storage_mode() and user_dek is not None:
        Session = _session_local()
        with Session() as db:
            row = (
                db.query(AccountArtifactModel)
                .where(
                    AccountArtifactModel.account_id == account_id,
                    AccountArtifactModel.relative_path == "output/output_curriculum_qa.json",
                )
                .first()
            )
            if row is None:
                return None
            try:
                if getattr(row, "hydration_status", "full") != "full":
                    return None
                plain_bytes = bytes(row.content) if is_sqlite_mode() else decrypt_bytes(user_dek, row.content)
                if hashlib.sha256(plain_bytes).hexdigest() != str(row.sha256):
                    logger.warning("Hash mismatch for curriculum QA artifact for %s", account_id)
                    return None
                return json.loads(plain_bytes.decode("utf-8"))
            except Exception as exc:
                logger.warning("Failed loading curriculum QA artifact for %s: %s", account_id, exc)
                return None

    # Keep filesystem fallback for local/backend-cloud hybrid runs and tests.
    qa_path = account_qa_output_path(base_dir=BASE_DIR, account_id=account_id)
    if not qa_path.exists():
        return None
    return json.loads(qa_path.read_text(encoding="utf-8"))


def list_lessons(account_id: str = "admin", user_dek: bytes | None = None) -> list[Lesson]:
    raw = _load_curriculum_qa(account_id=account_id, user_dek=user_dek)
    if raw is None:
        return []
    topics = raw.get("curriculum", {}).get("topics", [])
    qa_sets = raw.get("qa_sets", [])
    if not isinstance(qa_sets, list):
        qa_sets = []
    qa_by_topic: dict[str, dict] = {}
    for qa in qa_sets:
        if isinstance(qa, dict) and qa.get("questions"):
            qa_by_topic[str(qa.get("topic_id", ""))] = qa
    lessons: list[Lesson] = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        topic_id = str(topic.get("id", ""))
        if topic_id not in qa_by_topic:
            continue
        lessons.append(
            Lesson(
                id=topic_id,
                title=str(topic.get("title", "Untitled Topic")),
                objective=str(topic.get("summary", "")),
                content_blocks=[str(topic.get("summary", ""))] if topic.get("summary") else [],
                total_checkpoints=1,
                tags=_derive_lesson_tags({"topic": str(topic.get("title", "")), "title": str(topic.get("title", ""))}),
            )
        )
    return lessons


def list_account_lessons(account_id: str = "admin", user_dek: bytes | None = None) -> list[Lesson]:
    store = AccountStore(BASE_DIR / "engine" / "accounts" / "accounts.json")
    try:
        progress = store.get_lesson_progress(username=account_id, user_dek=user_dek)
    except AccountDataKeyMismatch:
        raise
    except Exception as exc:
        logger.warning("Failed listing account lessons for %s: %s", account_id, exc)
        progress = {}

    full_lessons = progress.get("lessons", {}) if isinstance(progress, dict) else {}
    active_id = progress.get("active_lesson_id") if isinstance(progress, dict) else None
    lesson_items = _summarize_account_lesson_progress(progress)

    if _is_db_storage_mode():
        merged_states = {str(k): v for k, v in full_lessons.items() if isinstance(v, dict)}
    else:
        file_progress = _read_filesystem_lesson_progress(account_id)
        file_lessons = file_progress.get("lessons", {}) if isinstance(file_progress, dict) else {}
        file_active = file_progress.get("active_lesson_id") if isinstance(file_progress, dict) else None
        active_id = active_id or file_active

        merged_states: dict[str, dict] = {}
        if isinstance(file_lessons, dict):
            merged_states.update({str(k): v for k, v in file_lessons.items() if isinstance(v, dict)})
        if isinstance(full_lessons, dict):
            for lesson_id, state in full_lessons.items():
                if not isinstance(state, dict):
                    continue
                existing = merged_states.get(str(lesson_id))
                if existing is None or str(state.get("updated_at", "")) >= str(existing.get("updated_at", "")):
                    merged_states[str(lesson_id)] = state

        item_ids = {str(item.get("lesson_id", "")) for item in lesson_items if isinstance(item, dict)}
        for lesson_id, state in merged_states.items():
            if lesson_id in item_ids:
                continue
            checkpoints = state.get("checkpoints", [])
            total = len(checkpoints) if isinstance(checkpoints, list) else 0
            current = int(state.get("current_checkpoint_index", 0))
            completed = bool(state.get("completed", False))
            lesson_items.append({
                "lesson_id": lesson_id,
                "title": str(state.get("title", lesson_id)),
                "topic": str(state.get("topic", "")),
                "status": "completed" if completed else ("active" if str(active_id) == lesson_id else "in_progress"),
                "current_checkpoint": min(max(0, current), max(0, total)),
                "total_checkpoints": total,
                "attempt_count": len(state.get("attempts", [])) if isinstance(state.get("attempts", []), list) else 0,
                "last_updated_at": str(state.get("updated_at", "")),
            })

    lessons = [_lesson_from_account_item(item, merged_states) for item in lesson_items if isinstance(item, dict)]
    lessons.sort(key=lambda lesson: lesson.content_blocks[-1] if lesson.content_blocks else "", reverse=True)
    return lessons


def _read_filesystem_lesson_progress(account_id: str) -> dict:
    path = BASE_DIR / "engine" / "accounts" / "accounts.json"
    if not path.exists():
        return {"active_lesson_id": None, "lessons": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        user = payload.get("users", {}).get(account_id, {})
        progress = user.get("lesson_progress", {})
        return progress if isinstance(progress, dict) else {"active_lesson_id": None, "lessons": {}}
    except Exception as exc:
        logger.warning("Failed reading filesystem lesson progress for %s: %s", account_id, exc)
        return {"active_lesson_id": None, "lessons": {}}


def _lesson_from_account_item(item: dict, full_lessons: dict[str, dict]) -> Lesson:
    title = str(item.get("title", item.get("lesson_id", "Lesson")))
    status = str(item.get("status", "in_progress"))
    current = int(item.get("current_checkpoint", 0))
    total = int(item.get("total_checkpoints", 0))
    objective = f"status={status}, checkpoint={current + 1}/{max(1, total)}"
    lesson_id = str(item.get("lesson_id", title))

    checkpoints: list[LessonCheckpoint] = []
    materials: list[dict] = []
    full_state = full_lessons.get(lesson_id)
    if isinstance(full_state, dict):
        raw_checkpoints = full_state.get("checkpoints", [])
        if isinstance(raw_checkpoints, list):
            for cp in raw_checkpoints:
                if isinstance(cp, dict):
                    checkpoints.append(LessonCheckpoint(
                        title=str(cp.get("title", "")),
                        difficulty=str(cp.get("difficulty", "")),
                        num_questions=int(cp.get("num_questions", 0)),
                        pass_threshold_percent=float(cp.get("pass_threshold_percent", 0)),
                        question_mode=str(cp.get("question_mode", "mcq")),
                        markdown=str(cp.get("markdown")) if cp.get("markdown") is not None else None,
                        quiz=cp.get("quiz", []) if isinstance(cp.get("quiz"), list) else [],
                        sources=cp.get("sources", []) if isinstance(cp.get("sources"), list) else [],
                    ))
        raw_materials = full_state.get("materials", [])
        if isinstance(raw_materials, list):
            # Lesson listings are used by the dashboard, chat bootstrap, and the
            # save picker. Keep them lightweight; bodies are fetched on demand.
            materials = [
                {
                    key: material[key]
                    for key in ("material_id", "kind", "title", "created_at")
                    if key in material
                }
                for material in raw_materials
                if isinstance(material, dict)
            ]

    return Lesson(
        id=lesson_id,
        title=title,
        objective=objective,
        content_blocks=[
            f"topic: {item.get('topic', '')}",
            f"attempts: {item.get('attempt_count', 0)}",
            f"last_updated_at: {item.get('last_updated_at', '')}",
        ],
        checkpoints=checkpoints,
        materials=materials,
        status=status,
        current_checkpoint=current,
        total_checkpoints=max(1, total),
        tags=_derive_lesson_tags(item),
    )


def _derive_lesson_tags(item: dict) -> list[str]:
    """Derive tags from topic and title tokens."""
    topic = str(item.get("topic", ""))
    title = str(item.get("title", ""))
    tokens = re.findall(r"[a-z0-9]+", topic.lower()) + re.findall(r"[a-z0-9]+", title.lower())
    stopwords = {"lesson", "on", "about", "the", "a", "an", "and", "or", "of", "in", "to", "for", "is", "with", "basic", "advanced", "introduction", "fundamentals", "tutorial", "guide", "overview", "part", "1", "2", "3", "4", "5"}
    tags = []
    for token in tokens:
        if token not in stopwords and len(token) > 2:
            tags.append(token)
    seen = set()
    unique = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique.append(t)
        if len(unique) >= 5:
            break
    return unique


def get_lesson(lesson_id: str, account_id: str = "admin", user_dek: bytes | None = None) -> Lesson | None:
    all_lessons = [
        *list_account_lessons(account_id=account_id, user_dek=user_dek),
        *list_lessons(account_id=account_id, user_dek=user_dek),
    ]
    for lesson in all_lessons:
        if lesson.id == lesson_id:
            return lesson
    query_key = _lesson_text_key(lesson_id)
    for lesson in all_lessons:
        if query_key and query_key == _lesson_text_key(lesson.title):
            return lesson
    fuzzy = _best_lesson_match(lesson_id, all_lessons)
    if fuzzy is not None:
        return fuzzy
    return None


def _lesson_text_key(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    tokens = [token for token in normalized.split() if token and token not in {"lesson", "on", "about", "the", "a", "an"}]
    singularized = [token[:-1] if token.endswith("s") and len(token) > 3 else token for token in tokens]
    return " ".join(singularized).strip()


def _best_lesson_match(query: str, lessons: list[Lesson]) -> Lesson | None:
    query_key = _lesson_text_key(query)
    if not query_key:
        return None
    best: Lesson | None = None
    best_score = 0.0
    for lesson in lessons:
        title_key = _lesson_text_key(lesson.title)
        id_key = _lesson_text_key(lesson.id)
        if not title_key and not id_key:
            continue
        if query_key in title_key or query_key in id_key or title_key in query_key or id_key in query_key:
            score = 0.95
        else:
            score = max(
                SequenceMatcher(None, query_key, title_key).ratio(),
                SequenceMatcher(None, query_key, id_key).ratio(),
            )
        if score > best_score:
            best = lesson
            best_score = score
    if best_score >= 0.65:
        return best
    return None


def _read_raw_text_docs(account_id: str, *, include_content: bool = True) -> list[Doc]:
    account_root = account_output_dir(base_dir=BASE_DIR, account_id=account_id).parent
    raw_text_dir = account_root / "output" / "raw_text"
    if not raw_text_dir.exists():
        return []
    docs: list[Doc] = []
    for txt_file in sorted(raw_text_dir.glob("*.txt")):
        rel_path = str(txt_file.relative_to(account_root)).replace("\\", "/")
        doc_id = _doc_id_for_relative_path("raw", rel_path)
        if include_content:
            text = txt_file.read_text(encoding="utf-8", errors="replace")
            chunks = _chunk_text(doc_id, text)
            chunk_count = len(chunks)
        else:
            chunks = []
            chunk_count = _count_chunks_from_file(txt_file)
        docs.append(
            Doc(
                id=doc_id,
                name=txt_file.name,
                uploaded_at=datetime.fromtimestamp(txt_file.stat().st_mtime, tz=UTC),
                chunks=chunks,
                chunk_count=chunk_count,
            )
        )
    return docs


def _count_chunks_from_file(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return _count_chunks(text)
    except Exception:
        return 0


def _read_uploaded_docs(account_id: str, *, include_content: bool = True) -> list[Doc]:
    docs_dir = account_documents_dir(base_dir=BASE_DIR, account_id=account_id)
    if not docs_dir.exists():
        return []
    account_root = docs_dir.parent
    docs: list[Doc] = []
    for file_path in sorted(docs_dir.iterdir()):
        if not file_path.is_file() or file_path.name.endswith(".embeddings.json"):
            continue
        rel_path = str(file_path.relative_to(account_root)).replace("\\", "/")
        doc_id = _doc_id_for_relative_path("upload", rel_path)
        stat = file_path.stat()
        if include_content:
            try:
                text = _cached_extract_text(str(file_path), stat.st_mtime, stat.st_size)
            except Exception as exc:
                logger.warning("Skipping unreadable document %s: %s", file_path, exc)
                continue
            chunks = _chunk_text(doc_id, text)
            chunk_count = len(chunks)
        else:
            chunks = []
            try:
                text = _cached_extract_text(str(file_path), stat.st_mtime, stat.st_size)
                chunk_count = _count_chunks(text)
            except Exception:
                chunk_count = 0
        docs.append(
            Doc(
                id=doc_id,
                name=file_path.name,
                uploaded_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                chunks=chunks,
                chunk_count=chunk_count,
            )
        )
    return docs


def _extract_text_from_bytes(name: str, content: bytes) -> str:
    import tempfile
    import os
    suffix = Path(name).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        text = _extract_text(tmp_path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    return text


@functools.lru_cache(maxsize=128)
def _cached_extract_text(path_str: str, mtime: float, size: int) -> str:
    """Cache text extraction results keyed by file identity (path, mtime, size)."""
    return _extract_text(Path(path_str))


_text_from_bytes_cache: dict[tuple[str, str], str] = {}
_hydration_inflight: set[tuple[str, str]] = set()
_hydration_lock = __import__("threading").Lock()

_doc_cache: dict[tuple[str, str], Doc] = {}
_doc_cache_max_size = 64


def _invalidate_doc_cache(account_id: str, doc_id: str | None = None) -> None:
    if doc_id is None:
        for key in [key for key in _doc_cache if key[0] == account_id]:
            _doc_cache.pop(key, None)
        return
    _doc_cache.pop((account_id, doc_id), None)


def _get_cached_text_from_bytes(name: str, sha256: str, content_bytes: bytes) -> str:
    """Cache text extraction from bytes keyed by (name, sha256)."""
    key = (name, sha256)
    cached = _text_from_bytes_cache.get(key)
    if cached is not None:
        return cached
    text = _extract_text_from_bytes(name, content_bytes)
    _text_from_bytes_cache[key] = text
    return text


def _doc_id_for_relative_path(prefix: str, relative_path: str) -> str:
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:10]
    stem = Path(relative_path).stem
    return f"{_safe_doc_id(prefix, stem)}_{digest}"


def _stateless_doc_rows(db, account_id: str, *, include_deleted: bool = False, include_content: bool = True):
    query = (
        db.query(AccountArtifactModel)
        .where(
            AccountArtifactModel.account_id == account_id,
            AccountArtifactModel.relative_path.like("documents/%")
            | AccountArtifactModel.relative_path.like("output/raw_text/%"),
        )
    )
    if not include_content:
        query = query.options(
            load_only(
                AccountArtifactModel.account_id,
                AccountArtifactModel.relative_path,
                AccountArtifactModel.sha256,
                AccountArtifactModel.size_bytes,
                AccountArtifactModel.updated_at,
                AccountArtifactModel.deleted_at,
                AccountArtifactModel.expires_at,
                AccountArtifactModel.hydration_status,
            )
        )
    if not include_deleted:
        query = query.where(AccountArtifactModel.deleted_at.is_(None))
    return query.all()


def _doc_from_artifact_metadata(row) -> Doc | None:
    if row.relative_path.endswith(".embeddings.json"):
        return None
    name = Path(row.relative_path).name
    prefix = "upload" if row.relative_path.startswith("documents/") else "raw"
    doc_id = _doc_id_for_relative_path(prefix, row.relative_path)
    return Doc(id=doc_id, name=name, uploaded_at=row.updated_at, chunks=[])


def _chunk_counts_from_embedding_artifacts(
    db,
    account_id: str,
    user_dek: bytes | None = None,
) -> dict[str, DocEmbeddingMetadata]:
    rows = (
        db.query(AccountArtifactModel)
        .where(
            AccountArtifactModel.account_id == account_id,
            AccountArtifactModel.relative_path.like("documents/%.embeddings.json")
            | AccountArtifactModel.relative_path.like("output/raw_text/%.embeddings.json"),
        )
        .all()
    )
    metadata_by_doc: dict[str, DocEmbeddingMetadata] = {}
    for row in rows:
        try:
            if getattr(row, "hydration_status", "full") != "full":
                continue
            if is_sqlite_mode():
                payload = bytes(row.content)
            elif user_dek is not None:
                payload = decrypt_bytes(user_dek, row.content)
            else:
                continue
            data = json.loads(payload.decode("utf-8"))
            chunks = data.get("chunks", [])
            if not isinstance(chunks, list):
                continue
            doc_relative_path = str(row.relative_path)[: -len(".embeddings.json")]
            prefix = "upload" if doc_relative_path.startswith("documents/") else "raw"
            doc_id = _doc_id_for_relative_path(prefix, doc_relative_path)
            metadata_by_doc[doc_id] = DocEmbeddingMetadata(
                model=data.get("model") if isinstance(data.get("model"), str) else None,
                dimension=data.get("dimension") if isinstance(data.get("dimension"), int) else None,
                provider=data.get("provider") if isinstance(data.get("provider"), str) else None,
                runtime=data.get("runtime") if isinstance(data.get("runtime"), str) else None,
                chunk_count=len(chunks),
            )
        except Exception as exc:
            logger.warning("Failed reading chunk count from embeddings artifact %s: %s", row.relative_path, exc)
    return metadata_by_doc


def _read_stateless_docs(
    account_id: str,
    user_dek: bytes | None = None,
    *,
    include_content: bool = True,
) -> list[Doc]:
    Session = _session_local()

    docs: list[Doc] = []
    with Session() as db:
        embedding_metadata = _chunk_counts_from_embedding_artifacts(
            db,
            account_id,
            user_dek=user_dek,
        )
        rows = _stateless_doc_rows(db, account_id, include_content=include_content)
        for row in rows:
            if row.relative_path.endswith(".embeddings.json"):
                continue
            try:
                name = Path(row.relative_path).name
                prefix = "upload" if row.relative_path.startswith("documents/") else "raw"
                doc_id = _doc_id_for_relative_path(prefix, row.relative_path)
                embedding = embedding_metadata.get(doc_id)
                chunk_count = embedding.chunk_count if embedding else 0
                if getattr(row, "hydration_status", "full") != "full":
                    if chunk_count == 0:
                        continue
                chunks: list[DocChunk] = []
                if include_content:
                    if is_sqlite_mode():
                        plain_bytes = bytes(row.content)
                    elif user_dek is not None:
                        plain_bytes = decrypt_bytes(user_dek, row.content)
                    else:
                        docs.append(
                            Doc(
                                id=doc_id,
                                name=name,
                                uploaded_at=row.updated_at,
                                chunks=[],
                                chunk_count=0,
                                embedding=embedding,
                            )
                        )
                        continue
                    if hashlib.sha256(plain_bytes).hexdigest() != str(row.sha256):
                        logger.warning("Hash mismatch for DB document %s", row.relative_path)
                        continue
                    text = _get_cached_text_from_bytes(name, str(row.sha256), plain_bytes)
                    chunks = _chunk_text(doc_id, text)
                    chunk_count = len(chunks)
                docs.append(
                    Doc(
                        id=doc_id,
                        name=name,
                        uploaded_at=row.updated_at,
                        chunks=chunks,
                        chunk_count=chunk_count,
                        embedding=embedding,
                    )
                )
            except Exception as exc:
                logger.warning("Skipping unreadable DB document %s: %s", row.relative_path, exc)
                continue
    return docs


def _local_embedding_metadata(account_id: str, doc: Doc) -> DocEmbeddingMetadata | None:
    docs_dir = account_documents_dir(base_dir=BASE_DIR, account_id=account_id)
    raw_text_dir = account_output_dir(base_dir=BASE_DIR, account_id=account_id) / "raw_text"
    for base in (docs_dir, raw_text_dir):
        embeddings_file = base / f"{doc.name}.embeddings.json"
        if not embeddings_file.exists():
            continue
        try:
            data = json.loads(embeddings_file.read_text(encoding="utf-8"))
            chunks = data.get("chunks", [])
            if not isinstance(chunks, list):
                return None
            return DocEmbeddingMetadata(
                model=data.get("model") if isinstance(data.get("model"), str) else None,
                dimension=data.get("dimension") if isinstance(data.get("dimension"), int) else None,
                provider=data.get("provider") if isinstance(data.get("provider"), str) else None,
                runtime=data.get("runtime") if isinstance(data.get("runtime"), str) else None,
                chunk_count=len(chunks),
            )
        except Exception as exc:
            logger.warning("Failed reading local embedding metadata %s: %s", embeddings_file, exc)
            return None
    return None


def save_embeddings(doc_id: str, embeddings_data: dict, account_id: str = "admin", user_dek: bytes | None = None, doc_tags: list[str] | None = None) -> None:
    """Save chunk embeddings JSON for a specific document."""
    if doc_tags:
        embeddings_data["tags"] = doc_tags
    if _is_db_storage_mode():
        if not is_sqlite_mode() and user_dek is None:
            raise ValueError("user_dek is required for stateless E2EE embeddings storage")
        from backend.app.services.db import SessionLocal
        from backend.app.services.models import AccountArtifactModel
        Session = _session_local()

        with Session() as db:
            rows = (
                db.query(AccountArtifactModel)
                .where(
                    AccountArtifactModel.account_id == account_id,
                    AccountArtifactModel.relative_path.like("documents/%") | AccountArtifactModel.relative_path.like("output/raw_text/%"),
                    ~AccountArtifactModel.relative_path.like("%.embeddings.json"),
                )
                .all()
            )
            target_path = None
            for row in rows:
                prefix = "upload" if row.relative_path.startswith("documents/") else "raw"
                if _doc_id_for_relative_path(prefix, row.relative_path) == doc_id:
                    target_path = row.relative_path
                    break

            if not target_path:
                raise ValueError(f"Document with ID {doc_id} not found.")

            embeddings_path = f"{target_path}.embeddings.json"
            content_bytes = json.dumps(embeddings_data).encode("utf-8")
            if is_sqlite_mode():
                stored_content = content_bytes
            else:
                stored_content = encrypt_bytes(user_dek, content_bytes)
            sha256_hash = hashlib.sha256(content_bytes).hexdigest()

            exists = db.query(AccountArtifactModel).where(
                AccountArtifactModel.account_id == account_id,
                AccountArtifactModel.relative_path == embeddings_path
            ).first()

            if exists:
                exists.content = stored_content
                exists.sha256 = sha256_hash
                exists.size_bytes = len(stored_content)
                exists.updated_at = datetime.now(UTC)
            else:
                db.add(AccountArtifactModel(
                    account_id=account_id,
                    relative_path=embeddings_path,
                    content=stored_content,
                    sha256=sha256_hash,
                    size_bytes=len(stored_content),
                    updated_at=datetime.now(UTC),
                ))
            db.commit()
    else:
        doc = get_doc(doc_id, account_id=account_id)
        if not doc:
            raise ValueError(f"Document with ID {doc_id} not found.")

        docs_dir = account_documents_dir(base_dir=BASE_DIR, account_id=account_id)
        raw_text_dir = account_output_dir(base_dir=BASE_DIR, account_id=account_id) / "raw_text"

        target_file = docs_dir / doc.name
        if not target_file.exists():
            target_file = raw_text_dir / doc.name

        if not target_file.exists():
            raise ValueError(f"Local file for document {doc.name} not found.")

        embeddings_file = target_file.parent / f"{target_file.name}.embeddings.json"
        embeddings_file.write_text(json.dumps(embeddings_data, indent=2), encoding="utf-8")


def _attach_embeddings(account_id: str, docs: list[Doc], user_dek: bytes | None = None) -> None:
    if not docs:
        return

    if _is_db_storage_mode():
        from backend.app.services.db import SessionLocal
        from backend.app.services.models import AccountArtifactModel
        Session = _session_local()

        with Session() as db:
            rows = (
                db.query(AccountArtifactModel)
                .where(
                    AccountArtifactModel.account_id == account_id,
                    AccountArtifactModel.relative_path.like("documents/%.embeddings.json") | AccountArtifactModel.relative_path.like("output/raw_text/%.embeddings.json")
                )
                .all()
            )

            embeddings_by_relpath = {}
            for row in rows:
                if getattr(row, "hydration_status", "full") != "full":
                    continue
                try:
                    if is_sqlite_mode():
                        data = json.loads(bytes(row.content).decode("utf-8"))
                    else:
                        if user_dek is None:
                            continue
                        plain_bytes = decrypt_bytes(user_dek, row.content)
                        data = json.loads(plain_bytes.decode("utf-8"))
                    orig_path = row.relative_path.removesuffix(".embeddings.json")
                    embeddings_by_relpath[orig_path] = data
                except Exception as exc:
                    logger.warning("Failed to decrypt/load embeddings artifact %s: %s", row.relative_path, exc)
                    continue

            for doc in docs:
                for orig_relpath, data in embeddings_by_relpath.items():
                    prefix = "upload" if orig_relpath.startswith("documents/") else "raw"
                    expected_id = _doc_id_for_relative_path(prefix, orig_relpath)
                    if expected_id == doc.id:
                        chunks_data = data.get("chunks", [])
                        if isinstance(chunks_data, list):
                            doc.embedding = DocEmbeddingMetadata(
                                model=data.get("model") if isinstance(data.get("model"), str) else None,
                                dimension=data.get("dimension") if isinstance(data.get("dimension"), int) else None,
                                provider=data.get("provider") if isinstance(data.get("provider"), str) else None,
                                runtime=data.get("runtime") if isinstance(data.get("runtime"), str) else None,
                                chunk_count=len(chunks_data),
                            )
                        doc_tags = data.get("tags")
                        if isinstance(doc_tags, list):
                            doc.tags = doc_tags
                        vector_by_chunk_id = {}
                        tag_by_chunk_id = {}
                        for item in chunks_data:
                            cid = item.get("chunk_id")
                            vec = item.get("vector")
                            if cid and isinstance(vec, list):
                                vector_by_chunk_id[cid] = vec
                            chunk_tags = item.get("tags")
                            if cid and isinstance(chunk_tags, list):
                                tag_by_chunk_id[cid] = chunk_tags

                        for chunk in doc.chunks:
                            if chunk.chunk_id in vector_by_chunk_id:
                                chunk.vector = vector_by_chunk_id[chunk.chunk_id]
                            if chunk.chunk_id in tag_by_chunk_id:
                                chunk.tags = tag_by_chunk_id[chunk.chunk_id]
                        break
    else:
        docs_dir = account_documents_dir(base_dir=BASE_DIR, account_id=account_id)
        raw_text_dir = account_output_dir(base_dir=BASE_DIR, account_id=account_id) / "raw_text"

        for doc in docs:
            target_file = docs_dir / doc.name
            if not target_file.exists():
                target_file = raw_text_dir / doc.name
            if not target_file.exists():
                continue

            embeddings_file = target_file.parent / f"{target_file.name}.embeddings.json"
            if embeddings_file.exists():
                try:
                    data = json.loads(embeddings_file.read_text(encoding="utf-8"))
                    chunks_data = data.get("chunks", [])
                    if isinstance(chunks_data, list):
                        doc.embedding = DocEmbeddingMetadata(
                            model=data.get("model") if isinstance(data.get("model"), str) else None,
                            dimension=data.get("dimension") if isinstance(data.get("dimension"), int) else None,
                            provider=data.get("provider") if isinstance(data.get("provider"), str) else None,
                            runtime=data.get("runtime") if isinstance(data.get("runtime"), str) else None,
                            chunk_count=len(chunks_data),
                        )
                    doc_tags = data.get("tags")
                    if isinstance(doc_tags, list):
                        doc.tags = doc_tags
                    vector_by_chunk_id = {}
                    tag_by_chunk_id = {}
                    for item in chunks_data:
                        cid = item.get("chunk_id")
                        vec = item.get("vector")
                        if cid and isinstance(vec, list):
                            vector_by_chunk_id[cid] = vec
                        chunk_tags = item.get("tags")
                        if cid and isinstance(chunk_tags, list):
                            tag_by_chunk_id[cid] = chunk_tags

                    for chunk in doc.chunks:
                        if chunk.chunk_id in vector_by_chunk_id:
                            chunk.vector = vector_by_chunk_id[chunk.chunk_id]
                        if chunk.chunk_id in tag_by_chunk_id:
                            chunk.tags = tag_by_chunk_id[chunk.chunk_id]
                except Exception as exc:
                    logger.warning("Failed to load local embeddings file %s: %s", embeddings_file, exc)


def list_docs(
    account_id: str = "admin",
    user_dek: bytes | None = None,
    *,
    include_content: bool = True,
    limit: int | None = None,
    offset: int = 0,
) -> list[Doc]:
    by_id: dict[str, Doc] = {}
    for doc in _read_raw_text_docs(account_id=account_id, include_content=include_content):
        by_id[doc.id] = doc
    for doc in _read_uploaded_docs(account_id=account_id, include_content=include_content):
        by_id[doc.id] = doc

    if _is_db_storage_mode():
        try:
            db_docs = _read_stateless_docs(
                account_id=account_id,
                user_dek=user_dek,
                include_content=include_content,
            )
            for doc in db_docs:
                by_id[doc.id] = doc
        except Exception as exc:
            logger.warning("Failed listing DB documents for %s; using local documents only: %s", account_id, exc)

    docs = list(by_id.values())
    for doc in docs:
        if doc.embedding is None:
            doc.embedding = _local_embedding_metadata(account_id, doc)
    def _uploaded_at_sort_key(doc: Doc) -> datetime:
        uploaded_at = doc.uploaded_at or datetime.min.replace(tzinfo=UTC)
        if uploaded_at.tzinfo is None:
            return uploaded_at.replace(tzinfo=UTC)
        return uploaded_at

    docs.sort(key=_uploaded_at_sort_key, reverse=True)

    if limit is not None:
        docs = docs[offset:offset + limit]
    elif offset:
        docs = docs[offset:]

    if include_content:
        _attach_embeddings(account_id, docs, user_dek)
    return docs


def get_doc(doc_id: str, account_id: str = "admin", user_dek: bytes | None = None) -> Doc | None:
    cache_key = (account_id, doc_id)
    if cache_key in _doc_cache:
        return _doc_cache[cache_key]

    if _is_db_storage_mode():
        Session = _session_local()
        try:
            with Session() as db:
                rows = _stateless_doc_rows(db, account_id, include_content=False)
                for row in rows:
                    doc = _doc_from_artifact_metadata(row)
                    if doc is None or doc.id != doc_id:
                        continue
                    if not is_sqlite_mode() and user_dek is None:
                        return doc
                    try:
                        if is_sqlite_mode() and getattr(row, "hydration_status", "full") != "full":
                            if user_dek is None:
                                return doc
                            import threading
                            from backend.app.services.account_sync import hydrate_account_artifact
                            hydrate_key = (account_id, row.relative_path)
                            with _hydration_lock:
                                if hydrate_key in _hydration_inflight:
                                    return doc
                                _hydration_inflight.add(hydrate_key)
                            def _hydrate_wrapper():
                                try:
                                    hydrate_account_artifact(account_id, user_dek, row.relative_path)
                                finally:
                                    with _hydration_lock:
                                        _hydration_inflight.discard(hydrate_key)
                            threading.Thread(target=_hydrate_wrapper, daemon=True).start()
                            return doc
                        row = (
                            db.query(AccountArtifactModel)
                            .where(
                                AccountArtifactModel.account_id == account_id,
                                AccountArtifactModel.relative_path == row.relative_path,
                            )
                            .first()
                        )
                        if row is None:
                            return None
                        if is_sqlite_mode():
                            plain_bytes = bytes(row.content)
                        else:
                            plain_bytes = decrypt_bytes(user_dek, row.content)
                        if hashlib.sha256(plain_bytes).hexdigest() != str(row.sha256):
                            logger.warning("Hash mismatch for DB document %s", row.relative_path)
                            return None
                        text = _get_cached_text_from_bytes(doc.name, str(row.sha256), plain_bytes)
                        doc.chunks = _chunk_text(doc.id, text)
                        doc.chunk_count = len(doc.chunks)
                        if len(_doc_cache) >= _doc_cache_max_size:
                            _doc_cache.pop(next(iter(_doc_cache)))
                        _doc_cache[cache_key] = doc
                        return doc
                    except Exception as exc:
                        logger.warning("Skipping unreadable DB document %s: %s", row.relative_path, exc)
                        return None
        except Exception as exc:
            logger.warning("Failed reading DB document %s for %s; trying local documents: %s", doc_id, account_id, exc)
        for doc in list_docs(account_id=account_id, user_dek=user_dek):
            if doc.id == doc_id:
                if len(_doc_cache) >= _doc_cache_max_size:
                    _doc_cache.pop(next(iter(_doc_cache)))
                _doc_cache[cache_key] = doc
                return doc
        return None

    for doc in list_docs(account_id=account_id, user_dek=user_dek):
        if doc.id == doc_id:
            if len(_doc_cache) >= _doc_cache_max_size:
                _doc_cache.pop(next(iter(_doc_cache)))
            _doc_cache[cache_key] = doc
            return doc
    return None


def get_doc_texts_for_chunk_ids(
    chunk_ids: list[str],
    account_id: str = "admin",
    user_dek: bytes | None = None,
) -> list[str]:
    if not chunk_ids:
        return []
    wanted = set(chunk_ids)
    wanted_doc_ids = {chunk_id.rsplit("_chunk_", 1)[0] for chunk_id in wanted if "_chunk_" in chunk_id}
    if not wanted_doc_ids:
        return []

    found: list[str] = []
    if _is_db_storage_mode():
        if user_dek is None and not is_sqlite_mode():
            return []
        # Load all docs once instead of calling get_doc() per doc_id (N+1 fix).
        all_docs = _read_stateless_docs(
            account_id=account_id, user_dek=user_dek, include_content=True,
        )
        for doc in all_docs:
            if doc.id not in wanted_doc_ids:
                continue
            for chunk in doc.chunks:
                if chunk.chunk_id in wanted:
                    found.append(chunk.text)
        return found

    for doc in list_docs(account_id=account_id, user_dek=user_dek):
        if doc.id not in wanted_doc_ids:
            continue
        for chunk in doc.chunks:
            if chunk.chunk_id in wanted:
                found.append(chunk.text)
    return found


def search_docs(query: str, account_id: str = "admin", user_dek: bytes | None = None, filter_tags: list[str] | None = None) -> list[Doc]:
    query_lower = query.lower()
    result: list[Doc] = []
    for doc in list_docs(account_id=account_id, user_dek=user_dek):
        if filter_tags:
            filter_tags_lower = {t.lower() for t in filter_tags}
            if not any(t.lower() in filter_tags_lower for t in doc.tags):
                continue
        if query_lower in doc.name.lower() or any(query_lower in chunk.text.lower() for chunk in doc.chunks):
            result.append(doc)
    return result


def _summarize_account_lesson_progress(progress: dict) -> list[dict]:
    active = progress.get("active_lesson_id") if isinstance(progress, dict) else None
    lessons = progress.get("lessons", {}) if isinstance(progress, dict) else {}
    if not isinstance(lessons, dict):
        return []
    items: list[dict] = []
    for lesson_id, state in lessons.items():
        if not isinstance(state, dict):
            continue
        checkpoints = state.get("checkpoints", [])
        total = len(checkpoints) if isinstance(checkpoints, list) else 0
        current = int(state.get("current_checkpoint_index", 0))
        completed = bool(state.get("completed", False))
        if completed:
            status = "completed"
        elif str(active) == str(lesson_id):
            status = "active"
        else:
            status = "in_progress"
        attempts = state.get("attempts", [])
        items.append(
            {
                "lesson_id": str(lesson_id),
                "title": str(state.get("title", lesson_id)),
                "topic": str(state.get("topic", "")),
                "status": status,
                "current_checkpoint": min(max(0, current), max(0, total)),
                "total_checkpoints": total,
                "attempt_count": len(attempts) if isinstance(attempts, list) else 0,
                "last_updated_at": str(state.get("updated_at", "")),
                "last_accessed_at": str(state.get("last_accessed_at", "")),
            }
        )
    items.sort(key=lambda x: x.get("last_updated_at", ""), reverse=True)
    return items


def _sanitize_filename(name: str) -> str:
    cleaned = Path(name).name
    return "".join(ch for ch in cleaned if ch.isalnum() or ch in {"-", "_", ".", " "}).strip() or "uploaded.txt"


def add_doc(name: str, content: bytes, account_id: str = "admin", user_dek: bytes | None = None) -> Doc:
    if _is_db_storage_mode():
        if not is_sqlite_mode() and user_dek is None:
            raise ValueError("user_dek is required for stateless E2EE document storage")
        from backend.app.services.db import SessionLocal
        from backend.app.services.models import AccountArtifactModel


        safe_name = _sanitize_filename(name)
        relative_path = f"documents/{safe_name}"

        Session = _session_local()
        with Session() as db:
            exists = db.query(AccountArtifactModel).where(
                AccountArtifactModel.account_id == account_id,
                AccountArtifactModel.relative_path == relative_path
            ).first() is not None
            if exists:
                stem = Path(safe_name).stem
                suffix = Path(safe_name).suffix
                safe_name = f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"
                relative_path = f"documents/{safe_name}"

            if is_sqlite_mode():
                stored_content = content
            else:
                stored_content = encrypt_bytes(user_dek, content)
            sha256_hash = hashlib.sha256(content).hexdigest()

            row = AccountArtifactModel(
                account_id=account_id,
                relative_path=relative_path,
                content=stored_content,
                sha256=sha256_hash,
                size_bytes=len(stored_content),
                updated_at=datetime.now(UTC)
            )
            db.add(row)
            db.commit()

        text = _extract_text_from_bytes(safe_name, content)
        doc_id = _doc_id_for_relative_path("upload", relative_path)
        return Doc(
            id=doc_id,
            name=safe_name,
            uploaded_at=datetime.now(UTC),
            chunks=_chunk_text(doc_id, text),
        )

    docs_dir = account_documents_dir(base_dir=BASE_DIR, account_id=account_id)
    docs_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_filename(name)
    upload_path = docs_dir / safe_name
    if upload_path.exists():
        upload_path = docs_dir / f"{upload_path.stem}_{uuid.uuid4().hex[:6]}{upload_path.suffix}"
    upload_path.write_bytes(content)

    text = _extract_text(upload_path)
    rel_path = str(upload_path.relative_to(docs_dir.parent)).replace("\\", "/")
    doc_id = _doc_id_for_relative_path("upload", rel_path)
    return Doc(
        id=doc_id,
        name=upload_path.name,
        uploaded_at=datetime.fromtimestamp(upload_path.stat().st_mtime, tz=UTC),
        chunks=_chunk_text(doc_id, text),
    )



def _list_sessions() -> list[ChatSession]:
    bootstrap_data()
    raw = _read_json(SESSIONS_PATH)
    return [ChatSession.model_validate(item) for item in raw]


def _save_sessions(sessions: list[ChatSession]) -> None:
    _write_json(SESSIONS_PATH, [session.model_dump(mode="json") for session in sessions])


def _title_from_first_input(text: str, max_len: int = 48) -> str:
    title = " ".join(text.strip().split())
    if not title:
        return ""
    if len(title) <= max_len:
        return title
    trimmed = title[:max_len].rsplit(" ", 1)[0].strip()
    return trimmed or title[:max_len].strip()


def _decrypt_session_title(title: str | None, user_dek: bytes | None) -> str:
    if not title:
        return ""
    if user_dek is not None and not is_sqlite_mode() and title.startswith("enc:v1:"):
        try:
            return decrypt_text(user_dek, title)
        except Exception as exc:
            logger.warning("Failed decrypting chat session title: %s", exc)
            return ""
    return title


def _encrypt_session_title(title: str, user_dek: bytes | None) -> str:
    if not title:
        return ""
    if is_sqlite_mode():
        return title
    return encrypt_text(require_dek(user_dek, "chat session title"), title)


def _decrypt_chat_text(text: str, user_dek: bytes | None) -> str:
    if user_dek is not None and not is_sqlite_mode() and text.startswith("enc:v1:"):
        try:
            return decrypt_text(user_dek, text)
        except Exception as exc:
            logger.warning("Failed decrypting chat message: %s", exc)
            return "[Unable to decrypt message]"
    return text


def _chat_session_from_model(
    model: ChatSessionModel,
    user_dek: bytes | None = None,
    *,
    include_messages: bool = True,
) -> ChatSession:
    return ChatSession(
        id=model.id,
        title=_decrypt_session_title(getattr(model, "title", ""), user_dek),
        created_at=model.created_at,
        updated_at=model.updated_at,
        messages=[
            ChatMessage(
                role=msg.role,  # type: ignore[arg-type]
                text=_decrypt_chat_text(msg.text, user_dek),
                created_at=msg.created_at,
                lesson_id=msg.lesson_id,
                doc_chunk_ids=json.loads(msg.doc_chunk_ids_json),
            )
            for msg in model.messages
        ] if include_messages else [],
    )


def list_sessions(account_id: str = "admin", user_dek: bytes | None = None, limit: int = 20) -> list[ChatSession]:
    if db_enabled():
        Session = _session_local()
        with Session() as db:
            has_messages = exists().where(ChatMessageModel.session_id == ChatSessionModel.id)
            rows = (
                db.query(ChatSessionModel)
                .where(
                    ChatSessionModel.account_id == account_id,
                    (ChatSessionModel.title != "") | has_messages,
                )
                .order_by(ChatSessionModel.updated_at.desc())
                .limit(limit)
                .all()
            )
            return [s for s in (_chat_session_from_model(row, user_dek=user_dek) for row in rows) if s.title or s.messages]
    sessions = _list_sessions()
    sessions = [session for session in sessions if session.title or session.messages]
    sessions.sort(key=lambda s: s.updated_at or s.created_at, reverse=True)
    return sessions[:limit]


def create_session(account_id: str = "admin") -> ChatSession:
    session = ChatSession(id=f"session_{uuid.uuid4().hex[:10]}")
    if db_enabled():
        Session = _session_local()
        with Session() as db:
            row = ChatSessionModel(id=session.id, account_id=account_id, title=session.title, created_at=session.created_at)
            db.add(row)
            db.commit()
        return session
    sessions = _list_sessions()
    sessions.append(session)
    _save_sessions(sessions)
    return session


def get_session(session_id: str, account_id: str = "admin", user_dek: bytes | None = None) -> ChatSession | None:
    if db_enabled():
        Session = _session_local()
        with Session() as db:
            row = (
                db.query(ChatSessionModel)
                .options(selectinload(ChatSessionModel.messages))
                .where(ChatSessionModel.id == session_id, ChatSessionModel.account_id == account_id)
                .first()
            )
            if row is None:
                return None
            return _chat_session_from_model(row, user_dek=user_dek)
    for session in _list_sessions():
        if session.id == session_id:
            return session
    return None


def append_message(
    session_id: str, message: ChatMessage, account_id: str = "admin", user_dek: bytes | None = None
) -> ChatSession | None:
    if db_enabled():
        Session = _session_local()
        from backend.app.services.db import is_sqlite_mode
        # Retry a few times in case concurrent writers collide on seq_no.
        max_retries = 3
        for attempt in range(max_retries):
            with Session() as db:
                try:
                    if is_sqlite_mode():
                        # SQLite does not support SELECT ... FOR UPDATE.
                        session_row = (
                            db.query(ChatSessionModel)
                            .where(ChatSessionModel.id == session_id, ChatSessionModel.account_id == account_id)
                            .first()
                        )
                    else:
                        session_row = (
                            db.query(ChatSessionModel)
                            .where(ChatSessionModel.id == session_id, ChatSessionModel.account_id == account_id)
                            .with_for_update()
                            .first()
                        )
                    if session_row is None:
                        return None
                    max_seq = db.execute(
                        select(func.max(ChatMessageModel.seq_no)).where(ChatMessageModel.session_id == session_id)
                    ).scalar_one_or_none()
                    now = datetime.now(UTC)
                    if int(max_seq or 0) == 0 and message.role == "user" and not getattr(session_row, "title", ""):
                        session_row.title = _encrypt_session_title(_title_from_first_input(message.text), user_dek)
                    session_row.updated_at = now
                    msg_row = ChatMessageModel(
                        session_id=session_id,
                        seq_no=int(max_seq or 0) + 1,
                        role=message.role,
                        text=message.text if is_sqlite_mode() else encrypt_text(require_dek(user_dek, "chat message"), message.text),
                        created_at=message.created_at,
                        updated_at=now,
                        lesson_id=message.lesson_id,
                        doc_chunk_ids_json=json.dumps(message.doc_chunk_ids),
                    )
                    db.add(msg_row)
                    db.commit()
                    db.refresh(session_row)
                    return _chat_session_from_model(session_row, user_dek=user_dek)
                except IntegrityError:
                    db.rollback()
                    if attempt == max_retries - 1:
                        logger.error(
                            "Failed to append message to session %s after %d retries "
                            "(seq_no collision). Message dropped: role=%s, created_at=%s",
                            session_id, max_retries, message.role, message.created_at,
                        )
                    continue
        return None

    sessions = _list_sessions()
    target: ChatSession | None = None
    for session in sessions:
        if session.id == session_id:
            if not session.messages and message.role == "user" and not session.title:
                session.title = _title_from_first_input(message.text)
            session.messages.append(message)
            session.updated_at = datetime.now(UTC)
            target = session
            break
    if target is None:
        return None
    _save_sessions(sessions)
    return target


def _account_rewards_dir(account_id: str) -> Path:
    path = REWARDS_DIR / account_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _account_store() -> AccountStore:
    return AccountStore(BASE_DIR / "engine" / "accounts" / "accounts.json")


def _empty_rewards_dashboard(account_id: str) -> dict:
    return {
        "username": account_id,
        "summary": {
            "total_points": 0,
            "total_quizzes": 0,
            "total_correct": 0,
            "total_answered": 0,
            "accuracy_percent": 0.0,
            "updated_at": None,
        },
        "trend": [],
    }


def _safe_rewards_summary(account_id: str, user_dek: bytes | None = None) -> dict:
    store = _account_store()
    user = store.get_user(account_id, user_dek=user_dek)
    if user is None:
        return _empty_rewards_dashboard(account_id)["summary"]
    return store.build_dashboard_data(username=account_id, limit=1, user_dek=user_dek)["summary"]


def get_rewards_dashboard(account_id: str, limit: int = 20, user_dek: bytes | None = None) -> dict:
    store = _account_store()
    user = store.get_user(account_id, user_dek=user_dek)
    if user is None:
        payload = _empty_rewards_dashboard(account_id)
    else:
        payload = store.build_dashboard_data(username=account_id, limit=limit, user_dek=user_dek)
    session_logs = list_reward_session_logs(account_id=account_id, limit=min(limit, 50))
    quiz_logs = _synthesize_session_logs_from_quiz_history(user) if user is not None else []
    session_logs = _merge_activity_logs(session_logs, quiz_logs)
    payload["session_logs"] = session_logs
    return payload


def get_leaderboard(
    account_id: str, limit: int = 50, user_dek: bytes | None = None
) -> dict:
    """Global XP leaderboard across all accounts.

    Ranks every account by ``total_points`` (see ``AccountStore.list_leaderboard``), returns the
    top ``limit`` rows plus the caller's own entry (``me``) so the UI can highlight them and show
    their rank even when they fall outside the visible window.
    """
    store = _account_store()
    # Dashboard totals come from quiz history, while leaderboard entries use
    # cached profile counters. Reconcile the caller before calculating ranks so
    # every leaderboard load reflects the dashboard's current value.
    store.reconcile_account_counters_from_history(username=account_id, user_dek=user_dek)
    ranked = store.list_leaderboard()
    me = next((entry for entry in ranked if entry["account_id"] == account_id), None)
    return {
        "entries": ranked[: max(1, int(limit))],
        "total_users": len(ranked),
        "me": me,
    }


def _synthesize_session_logs_from_quiz_history(user: dict) -> list[dict]:
    history = user.get("quiz_history")
    if not isinstance(history, list) or not history:
        return []
    by_day: dict[str, dict] = {}
    for entry in history:
        submitted = entry.get("submitted_at", "")
        if not submitted:
            continue
        try:
            day = submitted[:10]
        except Exception:
            continue
        points = int(entry.get("points_earned", 0))
        quizzes = 1
        if day in by_day:
            by_day[day]["points_delta"] += points
            by_day[day]["quizzes_delta"] += quizzes
            by_day[day]["events"] += 1
        else:
            by_day[day] = {
                "session_id": f"synthetic_{day}",
                "started_at": f"{day}T00:00:00",
                "last_event_at": submitted,
                "points_delta": points,
                "quizzes_delta": quizzes,
                "events": 1,
            }
    result = list(by_day.values())
    result.sort(key=lambda x: x["last_event_at"], reverse=True)
    return result


def _merge_activity_logs(reward_logs: list[dict], quiz_logs: list[dict]) -> list[dict]:
    by_day: dict[str, dict] = {}

    for log in reward_logs:
        day = _activity_log_day(log)
        if not day:
            continue
        existing = by_day.get(day)
        if existing is None:
            started_at = str(log.get("started_at", f"{day}T00:00:00"))
            last_event_at = str(log.get("last_event_at", log.get("started_at", f"{day}T00:00:00")))
            by_day[day] = {
                "session_id": f"activity_{day}",
                "started_at": started_at,
                "last_event_at": last_event_at,
                "points_delta": int(log.get("points_delta", 0) or 0),
                "quizzes_delta": int(log.get("quizzes_delta", 0) or 0),
                "events": int(log.get("events", 0) or 0),
            }
            continue
        started_at = str(log.get("started_at", ""))
        last_event_at = str(log.get("last_event_at", ""))
        existing["started_at"] = min(
            str(existing.get("started_at", "")),
            started_at or str(existing.get("started_at", "")),
        )
        existing["last_event_at"] = max(str(existing.get("last_event_at", "")), last_event_at)
        existing["points_delta"] = int(existing.get("points_delta", 0)) + int(log.get("points_delta", 0) or 0)
        existing["quizzes_delta"] = int(existing.get("quizzes_delta", 0)) + int(log.get("quizzes_delta", 0) or 0)
        existing["events"] = int(existing.get("events", 0)) + int(log.get("events", 0) or 0)

    for log in quiz_logs:
        day = _activity_log_day(log)
        if not day:
            continue
        existing = by_day.get(day)
        if existing is None:
            by_day[day] = dict(log)
            continue
        started_at = str(log.get("started_at", ""))
        last_event_at = str(log.get("last_event_at", ""))
        existing["started_at"] = min(
            str(existing.get("started_at", "")),
            started_at or str(existing.get("started_at", "")),
        )
        existing["last_event_at"] = max(str(existing.get("last_event_at", "")), last_event_at)
        existing["points_delta"] = max(int(existing.get("points_delta", 0)), int(log.get("points_delta", 0) or 0))
        existing["quizzes_delta"] = max(int(existing.get("quizzes_delta", 0)), int(log.get("quizzes_delta", 0) or 0))
        existing["events"] = max(int(existing.get("events", 0)), int(log.get("events", 0) or 0))

    result = list(by_day.values())
    result.sort(key=lambda x: str(x.get("last_event_at", "")), reverse=True)
    return result


def _activity_log_day(log: dict) -> str:
    timestamp = str(log.get("last_event_at") or log.get("started_at") or "")
    if len(timestamp) < 10:
        return ""
    return timestamp[:10]


def start_reward_session_log(account_id: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    session_id = f"reward_session_{timestamp}_{uuid.uuid4().hex[:8]}"
    summary = _safe_rewards_summary(account_id)
    event = {
        "event": "start",
        "timestamp": datetime.now(UTC).isoformat(),
        "total_points": int(summary.get("total_points", 0)),
        "total_quizzes": int(summary.get("total_quizzes", 0)),
        "total_correct": int(summary.get("total_correct", 0)),
        "total_answered": int(summary.get("total_answered", 0)),
    }
    if db_enabled():
        Session = _session_local()
        with Session() as db:
            db.add(
                RewardSessionModel(
                    session_id=session_id,
                    account_id=account_id,
                    created_at=datetime.now(UTC),
                )
            )
            db.add(
                RewardEventModel(
                    session_id=session_id,
                    event="start",
                    timestamp=datetime.now(UTC),
                    total_points=event["total_points"],
                    total_quizzes=event["total_quizzes"],
                    total_correct=event["total_correct"],
                    total_answered=event["total_answered"],
                    note="",
                )
            )
            db.commit()
        return session_id
    path = _account_rewards_dir(account_id) / f"{session_id}.jsonl"
    path.write_text(json.dumps(event, ensure_ascii=True) + "\n", encoding="utf-8")
    return session_id


def append_reward_session_log(
    account_id: str,
    session_id: str,
    note: str | None = None,
    user_dek: bytes | None = None,
) -> bool:
    summary = _safe_rewards_summary(account_id)
    event = {
        "event": "snapshot",
        "timestamp": datetime.now(UTC).isoformat(),
        "total_points": int(summary.get("total_points", 0)),
        "total_quizzes": int(summary.get("total_quizzes", 0)),
        "total_correct": int(summary.get("total_correct", 0)),
        "total_answered": int(summary.get("total_answered", 0)),
        "note": note or "",
    }
    if db_enabled():
        Session = _session_local()
        with Session() as db:
            session_row = (
                db.query(RewardSessionModel)
                .where(RewardSessionModel.session_id == session_id, RewardSessionModel.account_id == account_id)
                .first()
            )
            if session_row is None:
                return False
            db.add(
                RewardEventModel(
                    session_id=session_id,
                    event="snapshot",
                    timestamp=datetime.now(UTC),
                    total_points=event["total_points"],
                    total_quizzes=event["total_quizzes"],
                    total_correct=event["total_correct"],
                    total_answered=event["total_answered"],
                    note=str(note or "") if is_sqlite_mode() else encrypt_text(require_dek(user_dek, "reward event note"), str(note or "")),
                )
            )
            db.commit()
            return True
    path = _account_rewards_dir(account_id) / f"{session_id}.jsonl"
    if not path.exists():
        return False
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=True) + "\n")
    return True


def list_reward_session_logs(account_id: str, limit: int = 20) -> list[dict]:
    rows: list[dict] = []
    if db_enabled():
        Session = _session_local()
        with Session() as db:
            sessions = (
                db.query(RewardSessionModel)
                .options(selectinload(RewardSessionModel.events))
                .where(RewardSessionModel.account_id == account_id)
                .order_by(RewardSessionModel.created_at.desc())
                .limit(max(1, limit))
                .all()
            )
            for session in sessions:
                if not session.events:
                    continue
                start = session.events[0]
                end = session.events[-1]
                rows.append(
                    {
                        "session_id": session.session_id,
                        "started_at": start.timestamp.isoformat(),
                        "last_event_at": end.timestamp.isoformat(),
                        "points_delta": int(end.total_points) - int(start.total_points),
                        "quizzes_delta": int(end.total_quizzes) - int(start.total_quizzes),
                        "events": len(session.events),
                    }
                )
    else:
        folder = _account_rewards_dir(account_id)
        files = sorted(folder.glob("reward_session_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[: max(1, limit)]:
            lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if not lines:
                continue
            events = [json.loads(line) for line in lines]
            start = events[0]
            end = events[-1]
            rows.append(
                {
                    "session_id": path.stem,
                    "started_at": str(start.get("timestamp", "")),
                    "last_event_at": str(end.get("timestamp", "")),
                    "points_delta": int(end.get("total_points", 0)) - int(start.get("total_points", 0)),
                    "quizzes_delta": int(end.get("total_quizzes", 0)) - int(start.get("total_quizzes", 0)),
                    "events": len(events),
                }
            )
    return rows


def _vector_norm(vector: list[float]) -> float:
    return sum(x * x for x in vector) ** 0.5


def search_chunks_by_vector(
    query_vector: list[float],
    top_k: int = 3,
    threshold: float = 0.35,
    account_id: str = "admin",
    user_dek: bytes | None = None,
    filter_tags: list[str] | None = None,
) -> list[dict]:
    """Return the top-K chunks most similar to query_vector using cosine similarity.

    When filter_tags is provided, only chunks from documents matching at least
    one tag are considered (OR semantics).

    Returns list of dicts: [{"chunk_id": str, "text": str, "doc_name": str, "score": float}]
    """
    docs = list_docs(account_id=account_id, user_dek=user_dek)
    if filter_tags:
        filter_tags_lower = {t.lower() for t in filter_tags}
        docs = [d for d in docs if any(t.lower() in filter_tags_lower for t in d.tags)]
    qv = query_vector
    q_norm = _vector_norm(qv)
    if q_norm < 1e-12:
        return []

    scored: list[dict] = []
    for doc in docs:
        for chunk in doc.chunks:
            if not chunk.vector:
                continue
            cv = chunk.vector
            if len(qv) != len(cv):
                logger.warning(
                    "Vector dimension mismatch: query=%d chunk=%d, skipping chunk %s",
                    len(qv), len(cv), getattr(chunk, 'chunk_id', '?'),
                )
                continue
            c_norm = getattr(chunk, '_cached_norm', None)
            if c_norm is None:
                c_norm = _vector_norm(cv)
                chunk._cached_norm = c_norm
            if c_norm < 1e-12:
                continue
            dot = sum(a * b for a, b in zip(qv, cv))
            sim = dot / (q_norm * c_norm)
            if sim > threshold:
                scored.append({
                    "chunk_id": chunk.chunk_id,
                    "text": chunk.text,
                    "doc_name": doc.name,
                    "score": round(sim, 4),
                })

    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored[:top_k]


_BIN_TTL_DAYS = 30


def _find_all_artifact_rows_for_doc(db, account_id: str, doc_id: str) -> list:
    rows = _stateless_doc_rows(db, account_id, include_deleted=True)
    target_relpath = None
    for row in rows:
        if row.relative_path.endswith(".embeddings.json"):
            continue
        prefix = "upload" if row.relative_path.startswith("documents/") else "raw"
        if _doc_id_for_relative_path(prefix, row.relative_path) == doc_id:
            target_relpath = row.relative_path
            break
    if target_relpath is None:
        return []
    return [
        r for r in rows
        if r.relative_path == target_relpath or r.relative_path == f"{target_relpath}.embeddings.json"
    ]


def delete_doc(doc_id: str, account_id: str = "admin", user_dek: bytes | None = None) -> bool:
    if _is_db_storage_mode():
        Session = _session_local()
        with Session() as db:
            all_rows = _find_all_artifact_rows_for_doc(db, account_id, doc_id)
            if not all_rows:
                return False
            now = datetime.now(UTC)
            expires = now + timedelta(days=_BIN_TTL_DAYS)
            for row in all_rows:
                row.deleted_at = now
                row.expires_at = expires
                row.updated_at = now

            name = Path(all_rows[0].relative_path).name
            bin_id = uuid.uuid4().hex[:16]
            db.add(BinItemModel(
                id=bin_id,
                account_id=account_id,
                item_type="document",
                item_id=doc_id,
                name=name,
                metadata_json=json.dumps({"relative_path": all_rows[0].relative_path}),
                deleted_at=now,
                expires_at=expires,
            ))
            db.commit()
        _invalidate_doc_cache(account_id, doc_id)
        return True

    docs_dir = account_documents_dir(base_dir=BASE_DIR, account_id=account_id)
    raw_text_dir = account_output_dir(base_dir=BASE_DIR, account_id=account_id) / "raw_text"

    for doc in list_docs(account_id=account_id, user_dek=user_dek):
        if doc.id != doc_id:
            continue
        target_file = docs_dir / doc.name
        if not target_file.exists():
            target_file = raw_text_dir / doc.name
        if not target_file.exists():
            return False

        trash_dir = target_file.parent / ".trash"
        trash_dir.mkdir(parents=True, exist_ok=True)
        original_parent = target_file.parent
        trash_path = trash_dir / doc.name
        if trash_path.exists():
            trash_path = trash_dir / f"{target_file.stem}_{uuid.uuid4().hex[:6]}{target_file.suffix}"
        target_file.rename(trash_path)

        original_embeddings = original_parent / f"{doc.name}.embeddings.json"
        if original_embeddings.exists():
            trash_embeddings = trash_dir / f"{doc.name}.embeddings.json"
            if not trash_embeddings.exists():
                original_embeddings.rename(trash_embeddings)

        if SessionLocal is not None:
            now = datetime.now(UTC)
            expires = now + timedelta(days=_BIN_TTL_DAYS)
            with SessionLocal() as db:
                bin_id = uuid.uuid4().hex[:16]
                db.add(BinItemModel(
                    id=bin_id,
                    account_id=account_id,
                    item_type="document",
                    item_id=doc_id,
                    name=doc.name,
                    metadata_json=json.dumps({"original_path": str(trash_path), "relative_path": str(trash_path.relative_to(BASE_DIR))}),
                    deleted_at=now,
                    expires_at=expires,
                ))
                db.commit()
        _invalidate_doc_cache(account_id, doc_id)
        return True

    return False


def restore_doc(bin_item_id: str, account_id: str = "admin", user_dek: bytes | None = None) -> bool:
    Session = _session_local()
    with Session() as db:
        bin_row = db.query(BinItemModel).where(
            BinItemModel.id == bin_item_id,
            BinItemModel.account_id == account_id,
            BinItemModel.item_type == "document",
        ).first()
        if bin_row is None:
            return False
        restored_doc_id = bin_row.item_id

        if _is_db_storage_mode():
            meta = json.loads(bin_row.metadata_json)
            relative_path = meta.get("relative_path")
            if not relative_path:
                return False

            all_artifact_rows = (
                db.query(AccountArtifactModel)
                .where(
                    AccountArtifactModel.account_id == account_id,
                    (AccountArtifactModel.relative_path == relative_path)
                    | (AccountArtifactModel.relative_path == f"{relative_path}.embeddings.json"),
                )
                .all()
            )
            for row in all_artifact_rows:
                row.deleted_at = None
                row.expires_at = None
                row.updated_at = datetime.now(UTC)
            db.delete(bin_row)
            db.commit()
            _invalidate_doc_cache(account_id, restored_doc_id)
            return True

        meta = json.loads(bin_row.metadata_json)
        original_path = meta.get("original_path")
        if not original_path:
            return False
        trash_path = Path(original_path)
        if not trash_path.exists():
            db.delete(bin_row)
            db.commit()
            return False

        restore_target = trash_path.parent.parent / trash_path.name
        if restore_target.exists():
            restore_target = restore_target.parent / f"{restore_target.stem}_{uuid.uuid4().hex[:6]}{restore_target.suffix}"
        trash_path.rename(restore_target)

        trash_embeddings = trash_path.parent / f"{trash_path.name}.embeddings.json"
        if trash_embeddings.exists():
            restore_embeddings = restore_target.parent / f"{restore_target.name}.embeddings.json"
            trash_embeddings.rename(restore_embeddings)

        db.delete(bin_row)
        db.commit()
        _invalidate_doc_cache(account_id, restored_doc_id)
        return True

def list_bin_items(account_id: str = "admin") -> list[dict]:
    Session = _session_local()
    with Session() as db:
        rows = (
            db.query(BinItemModel)
            .where(BinItemModel.account_id == account_id)
            .order_by(BinItemModel.deleted_at.desc())
            .all()
        )
        items = []
        now = datetime.now(UTC)
        for row in rows:
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            remaining = (expires_at - now).total_seconds() / 86400.0
            items.append({
                "id": row.id,
                "item_type": row.item_type,
                "item_id": row.item_id,
                "name": row.name,
                "deleted_at": row.deleted_at.isoformat(),
                "expires_at": row.expires_at.isoformat(),
                "days_remaining": round(max(0, remaining), 1),
            })
        return items


def permanently_delete_expired(account_id: str | None = None) -> int:
    Session = _session_local()
    deleted_count = 0
    with Session() as db:
        now = datetime.now(UTC)
        query = db.query(BinItemModel).where(BinItemModel.expires_at < now)
        if account_id:
            query = query.where(BinItemModel.account_id == account_id)
        expired_items = query.all()

        for item in expired_items:
            if item.item_type == "document":
                meta = json.loads(item.metadata_json)
                relative_path = meta.get("relative_path") or meta.get("original_path")
                if relative_path and _is_db_storage_mode():
                    artifact_rows = (
                        db.query(AccountArtifactModel)
                        .where(
                            AccountArtifactModel.account_id == item.account_id,
                            AccountArtifactModel.deleted_at.is_not(None),
                            (AccountArtifactModel.relative_path == relative_path)
                            | (AccountArtifactModel.relative_path == f"{relative_path}.embeddings.json"),
                        )
                        .all()
                    )
                    for ar in artifact_rows:
                        db.delete(ar)
                elif relative_path:
                    file_path = Path(relative_path)
                    if file_path.exists():
                        file_path.unlink(missing_ok=True)
                    embeddings_path = file_path.parent / f"{file_path.name}.embeddings.json"
                    if embeddings_path.exists():
                        embeddings_path.unlink(missing_ok=True)

            db.delete(item)
            if item.item_type == "document":
                _invalidate_doc_cache(item.account_id, item.item_id)
            deleted_count += 1

        if deleted_count > 0:
            db.commit()
    return deleted_count


def permanent_delete_bin_item(bin_item_id: str, account_id: str = "admin") -> bool:
    Session = _session_local()
    with Session() as db:
        bin_row = db.query(BinItemModel).where(
            BinItemModel.id == bin_item_id,
            BinItemModel.account_id == account_id,
        ).first()
        if bin_row is None:
            return False

        if bin_row.item_type == "document":
            deleted_doc_id = bin_row.item_id
            meta = json.loads(bin_row.metadata_json)
            relative_path = meta.get("relative_path") or meta.get("original_path")
            if relative_path and _is_db_storage_mode():
                artifact_rows = (
                    db.query(AccountArtifactModel)
                    .where(
                        AccountArtifactModel.account_id == account_id,
                        AccountArtifactModel.deleted_at.is_not(None),
                        (AccountArtifactModel.relative_path == relative_path)
                        | (AccountArtifactModel.relative_path == f"{relative_path}.embeddings.json"),
                    )
                    .all()
                )
                for ar in artifact_rows:
                    db.delete(ar)
            elif relative_path:
                file_path = Path(relative_path)
                if file_path.exists():
                    file_path.unlink(missing_ok=True)
                embeddings_path = file_path.parent / f"{file_path.name}.embeddings.json"
                if embeddings_path.exists():
                    embeddings_path.unlink(missing_ok=True)
            _invalidate_doc_cache(account_id, deleted_doc_id)

        db.delete(bin_row)
        db.commit()
        return True
