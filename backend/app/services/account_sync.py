"""Account-scoped sync and deferred encrypted-artifact hydration.

Sync compares metadata before transferring bytes; this avoids replacing a
newer artifact merely because another device initiated a pull first.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from backend.app.schemas import ChatMessage, ChatSession
from backend.app.services.crypto_local import decrypt_bytes, decrypt_text, decrypt_text_strict, encrypt_bytes, encrypt_text, require_dek
from backend.app.services.db import (
    SessionLocal, CloudSessionLocal,
    db_enabled, cloud_db_enabled, is_sqlite_mode, is_stateless_db_mode,
)
from backend.app.services.models import (
    AccountArtifactModel,
    AccountRegistryRecordModel,
    BinItemModel,
    ChatMessageModel,
    ChatSessionModel,
    RewardEventModel,
    RewardSessionModel,
)
from sqlalchemy.orm import load_only, selectinload
from engine.quiz.account_system import AccountStore
from engine.shared.account_paths import account_root, normalize_account_id

logger = logging.getLogger(__name__)


BASE_DIR = Path(__file__).resolve().parents[3]
ACCOUNTS_PATH = BASE_DIR / "engine" / "accounts" / "accounts.json"
CHAT_SESSIONS_PATH = BASE_DIR / "backend" / "data" / "chat" / "sessions.json"
REWARDS_DIR = BASE_DIR / "backend" / "data" / "rewards"
_QA_ARTIFACT_PATH = "output/output_curriculum_qa.json"
ContentPullMode = Literal["full", "essentials", "metadata"]
HYDRATION_FULL = "full"
HYDRATION_METADATA_ONLY = "metadata_only"
HYDRATION_FAILED = "failed"
HYDRATION_PENDING_STATUSES = (HYDRATION_METADATA_ONLY, HYDRATION_FAILED)
_HYDRATION_MAX_WORKERS = 4


@dataclass(slots=True)
class SyncReport:
    account_id: str
    chat_sessions: int = 0
    reward_sessions: int = 0
    uploaded_files: int = 0
    deleted_remote_files: int = 0
    downloaded_files: int = 0
    deleted_local_files: int = 0
    registry_synced: bool = False
    qa_artifact_synced: bool = False
    warnings: list[str] = field(default_factory=list)


def _account_store() -> AccountStore:
    return AccountStore(ACCOUNTS_PATH)


def _check_qa_presence(account_id: str, report: SyncReport) -> None:
    """Warn if lessons exist but no QA artifact was synced."""
    if report.qa_artifact_synced:
        return
    store = _account_store()
    try:
        lessons = store.list_lessons(username=account_id)
    except Exception:
        return
    if lessons and not report.qa_artifact_synced:
        report.warnings.append(
            f"Lessons exist ({len(lessons)}) but no QA artifact was synced. "
            "Quizzes may fail until the pipeline is run or QA is auto-generated."
        )


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _same_optional_timestamp(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return _ensure_aware(left) == _ensure_aware(right)


def _same_artifact_metadata(local_row, cloud_row) -> bool:
    return (
        local_row.sha256 == cloud_row.sha256
        and local_row.size_bytes == cloud_row.size_bytes
        and _same_optional_timestamp(local_row.updated_at, cloud_row.updated_at)
        and _same_optional_timestamp(local_row.deleted_at, cloud_row.deleted_at)
        and _same_optional_timestamp(local_row.expires_at, cloud_row.expires_at)
    )


def _is_deferred_artifact_path(relative_path: str) -> bool:
    return (
        relative_path.startswith("documents/")
        or relative_path.startswith("output/raw_text/")
        or relative_path.endswith(".embeddings.json")
    )


def _should_hydrate_artifact(relative_path: str, content_mode: ContentPullMode) -> bool:
    if content_mode == "full":
        return True
    if content_mode == "metadata":
        return False
    if relative_path.endswith(".embeddings.json"):
        return True
    return not _is_deferred_artifact_path(relative_path)


def _account_root_dir(account_id: str) -> Path:
    return account_root(base_dir=BASE_DIR, account_id=account_id)

# basically see if there is any local file
def _iter_local_files(account_id: str) -> list[Path]:
    root = _account_root_dir(account_id)
    if not root.exists(): #return blank if root does not exist
        return []
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files.append(path)
    return files


def _relative_account_path(account_id: str, path: Path) -> str:
    root = _account_root_dir(account_id)
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        raise RuntimeError(f"Path {path} is outside account root {root}")


def _safe_write_bytes(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def _is_read_only_fs_error(exc: Exception) -> bool:
    return isinstance(exc, OSError) and exc.errno == errno.EROFS


def _prune_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            next(path.iterdir())
        except StopIteration:
            path.rmdir()


def _remove_stale_local_files(root: Path, keep: set[Path]) -> int:
    deleted = 0
    if not root.exists():
        return deleted
    for path in sorted((p for p in root.rglob("*") if p.is_file()), reverse=True):
        if path in keep:
            continue
        path.unlink()
        deleted += 1
    _prune_empty_dirs(root)
    return deleted


def _load_local_account_record(account_id: str) -> dict:
    store = _account_store()
    record = store.get_user(account_id)
    if record is None:
        raise RuntimeError(f"Account '{account_id}' does not exist in {ACCOUNTS_PATH}.")
    return dict(record)


def _write_local_account_record(account_id: str, record: dict, dek: bytes | None = None) -> None:
    store = _account_store()
    payload = store._load()  # type: ignore[attr-defined]
    users = payload.setdefault("users", {})
    users[account_id] = record
    store._save(payload, dek=dek)  # type: ignore[attr-defined]


def _db_session():
    """Return a session to the cloud (remote) database."""
    if is_sqlite_mode():
        # In SQLite mode, the cloud DB is DATABASE_URL
        if not cloud_db_enabled():
            raise RuntimeError("DATABASE_URL is not set. Configure it for cloud sync.")
        assert CloudSessionLocal is not None
        return CloudSessionLocal()
    # In Postgres mode, the main DB IS the cloud DB
    if not db_enabled():
        raise RuntimeError("DATABASE_URL is not set.")
    assert SessionLocal is not None
    return SessionLocal()


def _local_session():
    """Return a session to the local database (SQLite or Postgres)."""
    if not db_enabled():
        raise RuntimeError("DATABASE_URL is not set.")
    assert SessionLocal is not None
    return SessionLocal()


def _load_local_chat_sessions(account_id: str) -> list[ChatSession]:
    if not CHAT_SESSIONS_PATH.exists():
        return []
    try:
        raw = json.loads(CHAT_SESSIONS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    sessions: list[ChatSession] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("account_id", account_id)) != account_id:
            continue
        sessions.append(ChatSession.model_validate(item))
    return sessions


def _load_existing_chat_sessions() -> list[dict]:
    if not CHAT_SESSIONS_PATH.exists():
        return []
    try:
        raw = json.loads(CHAT_SESSIONS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return raw if isinstance(raw, list) else []


def _write_chat_sessions(payload: list[dict]) -> None:
    CHAT_SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHAT_SESSIONS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_local_reward_sessions(account_id: str) -> list[Path]:
    account_dir = REWARDS_DIR / account_id
    if not account_dir.exists():
        return []
    return sorted(account_dir.glob("reward_session_*.jsonl"))


def _parse_iso_timestamp(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)


def _chat_session_to_payload(session: ChatSession, account_id: str) -> dict:
    payload = session.model_dump(mode="json")
    payload["account_id"] = account_id
    return payload


def _push_chat_sessions_to_cloud(account_id: str, user_dek: bytes | None) -> int:
    if is_sqlite_mode():
        # SQLite mode: read from local DB, write to cloud DB
        sessions = _load_local_chat_sessions_from_db(account_id)
        with _db_session() as cloud_db:
            return _write_chat_sessions_to_db(cloud_db, account_id, sessions, user_dek)
    # Filesystem mode: read from local filesystem, write to cloud DB
    sessions = _load_local_chat_sessions(account_id)
    with _db_session() as cloud_db:
        return _write_chat_sessions_to_db(cloud_db, account_id, sessions, user_dek)


def _load_local_chat_sessions_from_db(account_id: str) -> list[ChatSession]:
    """Load chat sessions from the local database (SQLite mode). Data is plaintext."""
    with _local_session() as db:
        rows = (
            db.query(ChatSessionModel)
            .options(selectinload(ChatSessionModel.messages))
            .where(ChatSessionModel.account_id == account_id)
            .order_by(ChatSessionModel.created_at.asc())
            .all()
        )
        sessions = []
        for row in rows:
            msgs = []
            for msg in sorted(row.messages, key=lambda m: m.seq_no):
                msgs.append(ChatMessage(
                    role=msg.role,
                    text=msg.text,
                    created_at=msg.created_at,
                    updated_at=msg.updated_at,
                    lesson_id=msg.lesson_id,
                    doc_chunk_ids=json.loads(msg.doc_chunk_ids_json),
                ))
            sessions.append(ChatSession(
                id=row.id,
                title=getattr(row, "title", ""),
                created_at=row.created_at,
                updated_at=row.updated_at,
                messages=msgs,
            ))
        return sessions


def _ensure_aware(dt: datetime | None) -> datetime:
    """Ensure a datetime is UTC-aware. SQLite stores naive datetimes."""
    if dt is None:
        return datetime.min.replace(tzinfo=UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _merge_chat_sessions(
    local_sessions: list[ChatSession],
    cloud_sessions: list[ChatSession],
) -> list[ChatSession]:
    """Two-way merge: keep newest parent, append extra messages from the other side."""
    cloud_by_id: dict[str, ChatSession] = {s.id: s for s in cloud_sessions}
    merged: list[ChatSession] = []
    seen: set[str] = set()

    for local in local_sessions:
        cloud = cloud_by_id.pop(local.id, None)
        seen.add(local.id)
        if cloud is None:
            merged.append(local)
        else:
            local_ts = _ensure_aware(local.updated_at or local.created_at)
            cloud_ts = _ensure_aware(cloud.updated_at or cloud.created_at)
            if cloud_ts > local_ts:
                base, other = cloud, local
            else:
                base, other = local, cloud
            base_msgs = list(base.messages)
            other_msgs = list(other.messages)
            if len(other_msgs) > len(base_msgs):
                base_msgs.extend(other_msgs[len(base_msgs):])
            merged.append(ChatSession(
                id=base.id,
                title=base.title or other.title,
                created_at=base.created_at,
                updated_at=max(local_ts, cloud_ts),
                messages=base_msgs,
            ))

    for cloud in cloud_sessions:
        if cloud.id not in seen:
            merged.append(cloud)

    return merged


def _merge_reward_sessions(
    local_sessions: list[dict],
    cloud_sessions: list[dict],
) -> list[dict]:
    """Two-way merge for reward sessions. Each session is a dict with session_id, events, updated_at."""
    cloud_by_id: dict[str, dict] = {s["session_id"]: s for s in cloud_sessions}
    merged: list[dict] = []
    seen: set[str] = set()

    for local in local_sessions:
        sid = local["session_id"]
        cloud = cloud_by_id.pop(sid, None)
        seen.add(sid)
        if cloud is None:
            merged.append(local)
        else:
            local_ts = _ensure_aware(local.get("updated_at"))
            cloud_ts = _ensure_aware(cloud.get("updated_at"))
            if cloud_ts > local_ts:
                base, other = cloud, local
            else:
                base, other = local, cloud
            merged_events = list(base["events"])
            seen_timestamps = {e.get("timestamp") for e in merged_events}
            for ev in other["events"]:
                if ev.get("timestamp") not in seen_timestamps:
                    merged_events.append(ev)
            merged.append({
                "session_id": sid,
                "events": merged_events,
                "updated_at": max(local_ts, cloud_ts),
            })

    for cloud in cloud_sessions:
        if cloud["session_id"] not in seen:
            merged.append(cloud)

    return merged


def _write_chat_sessions_to_db(db, account_id: str, sessions: list[ChatSession], user_dek: bytes | None) -> int:
    """Upsert chat sessions: insert new, update changed, skip unchanged. Diff-based child merge."""
    if user_dek is None:
        assert is_sqlite_mode(), "plaintext chat writes require local SQLite"
    existing = (
        db.query(ChatSessionModel)
        .where(ChatSessionModel.account_id == account_id)
        .order_by(ChatSessionModel.created_at.asc())
        .all()
    )
    existing_by_id = {row.id: row for row in existing}
    now = datetime.now(UTC)
    upserted = 0
    for session in sessions:
        row = existing_by_id.pop(session.id, None)
        local_ts = _ensure_aware(session.updated_at or session.created_at)
        if row is not None:
            cloud_ts = _ensure_aware(row.updated_at or row.created_at)
            if local_ts <= cloud_ts:
                continue
            row.updated_at = now
            row.title = session.title if user_dek is None else encrypt_text(require_dek(user_dek, "chat session title"), session.title)
            existing_seq_nos = {m.seq_no for m in row.messages}
            for idx, msg in enumerate(session.messages, start=1):
                if idx not in existing_seq_nos:
                    db.add(
                        ChatMessageModel(
                            session_id=session.id,
                            seq_no=idx,
                            role=msg.role,
                            text=msg.text if user_dek is None else encrypt_text(require_dek(user_dek, "chat message"), msg.text),
                            created_at=msg.created_at,
                            updated_at=now,
                            lesson_id=msg.lesson_id,
                            doc_chunk_ids_json=json.dumps(msg.doc_chunk_ids),
                        )
                    )
        else:
            row = ChatSessionModel(
                id=session.id,
                account_id=account_id,
                title=session.title if user_dek is None else encrypt_text(require_dek(user_dek, "chat session title"), session.title),
                created_at=session.created_at,
                updated_at=now,
            )
            db.add(row)
            for idx, msg in enumerate(session.messages, start=1):
                db.add(
                    ChatMessageModel(
                        session_id=session.id,
                        seq_no=idx,
                        role=msg.role,
                        text=msg.text if user_dek is None else encrypt_text(require_dek(user_dek, "chat message"), msg.text),
                        created_at=msg.created_at,
                        updated_at=now,
                        lesson_id=msg.lesson_id,
                        doc_chunk_ids_json=json.dumps(msg.doc_chunk_ids),
                    )
                )
        upserted += 1
    db.commit()
    return upserted


def _pull_chat_sessions_from_cloud(account_id: str, user_dek: bytes | None) -> int:
    if is_sqlite_mode():
        # SQLite mode: read encrypted from cloud DB, decrypt, merge with local, write back
        with _db_session() as cloud_db:
            cloud_rows = (
                cloud_db.query(ChatSessionModel)
                .options(selectinload(ChatSessionModel.messages))
                .where(ChatSessionModel.account_id == account_id)
                .order_by(ChatSessionModel.created_at.asc())
                .all()
            )
        cloud_sessions = []
        for s in cloud_rows:
            try:
                cloud_sessions.append(ChatSession(
                    id=s.id,
                    title=decrypt_text_strict(user_dek, s.title, field_name=f"chat session {s.id} title") if getattr(s, "title", "") else "",
                    created_at=s.created_at,
                    updated_at=s.updated_at,
                    messages=[
                        ChatMessage(
                            role=msg.role,
                            text=decrypt_text_strict(
                                user_dek,
                                msg.text,
                                field_name=f"chat session {s.id} message {msg.seq_no}",
                            ),
                            created_at=msg.created_at,
                            updated_at=msg.updated_at,
                            lesson_id=msg.lesson_id,
                            doc_chunk_ids=json.loads(msg.doc_chunk_ids_json),
                        )
                        for msg in sorted(s.messages, key=lambda m: m.seq_no)
                    ],
                ))
            except Exception as exc:
                raise RuntimeError(f"Aborting chat pull for {account_id}: cloud chat decrypt failed") from exc
        local_sessions = _load_local_chat_sessions_from_db(account_id)
        merged = _merge_chat_sessions(local_sessions, cloud_sessions)
        with _local_session() as local_db:
            return _write_chat_sessions_to_db(local_db, account_id, merged, None)

    # Filesystem mode: read from cloud DB, merge with existing filesystem sessions
    with _db_session() as db:
        cloud_rows = (
            db.query(ChatSessionModel)
            .options(selectinload(ChatSessionModel.messages))
            .where(ChatSessionModel.account_id == account_id)
            .order_by(ChatSessionModel.created_at.asc())
            .all()
        )

        existing = _load_existing_chat_sessions()
        local_account = [item for item in existing if str(item.get("account_id", account_id)) == account_id]
        preserved = [item for item in existing if str(item.get("account_id", account_id)) != account_id]

        local_chat_sessions = []
        for item in local_account:
            msgs = []
            for idx, m in enumerate(item.get("messages", []), start=1):
                msgs.append(ChatMessage(
                    role=m.get("role", "user"),
                    text=m.get("text", ""),
                    created_at=_parse_iso_timestamp(m.get("created_at")),
                    lesson_id=m.get("lesson_id"),
                    doc_chunk_ids=m.get("doc_chunk_ids", []),
                ))
            local_chat_sessions.append(ChatSession(
                id=item.get("id", ""),
                title=item.get("title", ""),
                created_at=_parse_iso_timestamp(item.get("created_at")),
                messages=msgs,
            ))

        cloud_chat_sessions = []
        for s in cloud_rows:
            try:
                cloud_chat_sessions.append(ChatSession(
                    id=s.id,
                    title=decrypt_text_strict(user_dek, s.title, field_name=f"chat session {s.id} title") if getattr(s, "title", "") else "",
                    created_at=s.created_at,
                    updated_at=s.updated_at,
                    messages=[
                        ChatMessage(
                            role=msg.role,
                            text=decrypt_text_strict(
                                user_dek,
                                msg.text,
                                field_name=f"chat session {s.id} message {msg.seq_no}",
                            ),
                            created_at=msg.created_at,
                            updated_at=msg.updated_at,
                            lesson_id=msg.lesson_id,
                            doc_chunk_ids=json.loads(msg.doc_chunk_ids_json),
                        )
                        for msg in s.messages
                    ],
                ))
            except Exception as exc:
                raise RuntimeError(f"Aborting chat pull for {account_id}: cloud chat decrypt failed") from exc

        merged = _merge_chat_sessions(local_chat_sessions, cloud_chat_sessions)

        exported: list[dict] = []
        for session in merged:
            exported.append(
                {
                    "id": session.id,
                    "title": session.title,
                    "account_id": account_id,
                    "created_at": session.created_at.isoformat(),
                    "messages": [
                        {
                            "role": msg.role,
                            "text": msg.text,
                            "created_at": msg.created_at.isoformat(),
                            "lesson_id": msg.lesson_id,
                            "doc_chunk_ids": msg.doc_chunk_ids,
                        }
                        for msg in session.messages
                    ],
                }
            )

        combined = preserved + exported
        combined.sort(key=lambda item: str(item.get("created_at", "")))
        try:
            _write_chat_sessions(combined)
        except Exception as exc:
            if not _is_read_only_fs_error(exc):
                raise
        return len(exported)


def _push_reward_sessions_to_cloud(account_id: str, user_dek: bytes | None) -> int:
    if is_sqlite_mode():
        # SQLite mode: read from local DB, write to cloud DB
        local_sessions = _load_local_reward_sessions_from_db(account_id)
        with _db_session() as cloud_db:
            return _write_reward_sessions_to_db(cloud_db, account_id, local_sessions, user_dek)
    # Filesystem mode
    local_files = _load_local_reward_sessions(account_id)
    with _db_session() as db:
        return _write_reward_sessions_from_files(db, account_id, local_files, user_dek)


def _load_local_reward_sessions_from_db(account_id: str) -> list[dict]:
    """Load reward sessions from local DB as dicts. Data is plaintext in SQLite mode."""
    with _local_session() as db:
        rows = (
            db.query(RewardSessionModel)
            .options(selectinload(RewardSessionModel.events))
            .where(RewardSessionModel.account_id == account_id)
            .order_by(RewardSessionModel.created_at.asc())
            .all()
        )
        result = []
        for row in rows:
            events = []
            for ev in sorted(row.events, key=lambda e: e.id or 0):
                events.append({
                    "event": ev.event,
                    "timestamp": ev.timestamp.isoformat() if ev.timestamp else "",
                    "total_points": int(ev.total_points),
                    "total_quizzes": int(ev.total_quizzes),
                    "total_correct": int(ev.total_correct),
                    "total_answered": int(ev.total_answered),
                    "note": ev.note,
                })
            result.append({
                "session_id": row.session_id,
                "events": events,
                "updated_at": row.updated_at,
            })
        return result


def _write_reward_sessions_to_db(db, account_id: str, sessions: list[dict], user_dek: bytes | None) -> int:
    """Upsert reward sessions: skip if incoming is not newer than existing."""
    if user_dek is None:
        assert is_sqlite_mode(), "plaintext reward writes require local SQLite"
    existing = (
        db.query(RewardSessionModel)
        .where(RewardSessionModel.account_id == account_id)
        .order_by(RewardSessionModel.created_at.asc())
        .all()
    )
    existing_by_id = {row.session_id: row for row in existing}
    now = datetime.now(UTC)
    upserted = 0
    for sess in sessions:
        session_id = sess["session_id"]
        incoming_ts = _ensure_aware(sess.get("updated_at"))
        row = existing_by_id.pop(session_id, None)
        if row is not None:
            cloud_ts = _ensure_aware(row.updated_at or row.created_at)
            if incoming_ts <= cloud_ts:
                continue
            for old_ev in list(row.events):
                db.delete(old_ev)
            row.updated_at = now
        else:
            row = RewardSessionModel(session_id=session_id, account_id=account_id, updated_at=now)
            db.add(row)
        for event in sess["events"]:
            db.add(
                RewardEventModel(
                    session_id=session_id,
                    event=str(event.get("event", "snapshot")),
                    timestamp=_parse_iso_timestamp(str(event.get("timestamp", ""))),
                    total_points=int(event.get("total_points", 0)),
                    total_quizzes=int(event.get("total_quizzes", 0)),
                    total_correct=int(event.get("total_correct", 0)),
                    total_answered=int(event.get("total_answered", 0)),
                    note=str(event.get("note", "")) if user_dek is None else encrypt_text(require_dek(user_dek, "reward event note"), str(event.get("note", ""))),
                    updated_at=now,
                )
            )
        upserted += 1
    db.commit()
    return upserted


def _write_reward_sessions_from_files(db, account_id: str, local_files, user_dek: bytes | None) -> int:
    """Upsert reward sessions from local JSONL files: skip if file not newer than existing row."""
    import os as _os
    existing = (
        db.query(RewardSessionModel)
        .where(RewardSessionModel.account_id == account_id)
        .order_by(RewardSessionModel.created_at.asc())
        .all()
    )
    existing_by_id = {row.session_id: row for row in existing}
    now = datetime.now(UTC)
    upserted = 0
    for path in local_files:
        session_id = path.stem
        file_mtime = datetime.fromtimestamp(_os.path.getmtime(path), tz=UTC)
        row = existing_by_id.pop(session_id, None)
        if row is not None:
            cloud_ts = _ensure_aware(row.updated_at or row.created_at)
            if file_mtime <= cloud_ts:
                continue
            for old_ev in list(row.events):
                db.delete(old_ev)
            row.updated_at = now
        else:
            row = RewardSessionModel(session_id=session_id, account_id=account_id, updated_at=now)
            db.add(row)
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        events = [json.loads(line) for line in lines]
        for event in events:
            db.add(
                RewardEventModel(
                    session_id=session_id,
                    event=str(event.get("event", "snapshot")),
                    timestamp=_parse_iso_timestamp(str(event.get("timestamp", ""))),
                    total_points=int(event.get("total_points", 0)),
                    total_quizzes=int(event.get("total_quizzes", 0)),
                    total_correct=int(event.get("total_correct", 0)),
                    total_answered=int(event.get("total_answered", 0)),
                    note=encrypt_text(require_dek(user_dek, "reward event note"), str(event.get("note", ""))),
                    updated_at=now,
                )
            )
        upserted += 1
    db.commit()
    return upserted


def _pull_reward_sessions_from_cloud(account_id: str, user_dek: bytes | None) -> int:
    if is_sqlite_mode():
        # SQLite mode: read from cloud DB, merge with local, write to local DB
        with _db_session() as cloud_db:
            cloud_rows = (
                cloud_db.query(RewardSessionModel)
                .options(selectinload(RewardSessionModel.events))
                .where(RewardSessionModel.account_id == account_id)
                .order_by(RewardSessionModel.created_at.asc())
                .all()
            )
        cloud_sessions = []
        for s in cloud_rows:
            events = []
            for ev in sorted(s.events, key=lambda e: e.id or 0):
                events.append({
                    "event": ev.event,
                    "timestamp": ev.timestamp.isoformat() if ev.timestamp else "",
                    "total_points": int(ev.total_points),
                    "total_quizzes": int(ev.total_quizzes),
                    "total_correct": int(ev.total_correct),
                    "total_answered": int(ev.total_answered),
                    "note": decrypt_text(user_dek, ev.note),
                })
            cloud_sessions.append({
                "session_id": s.session_id,
                "events": events,
                "updated_at": s.updated_at,
            })
        local_sessions = _load_local_reward_sessions_from_db(account_id)
        merged = _merge_reward_sessions(local_sessions, cloud_sessions)
        with _local_session() as local_db:
            return _write_reward_sessions_to_db(local_db, account_id, merged, None)

    # Filesystem mode: read from cloud DB, merge with local filesystem sessions
    account_dir = REWARDS_DIR / account_id
    local_writable = True
    try:
        account_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        if _is_read_only_fs_error(exc):
            local_writable = False
        else:
            raise

    with _db_session() as db:
        cloud_rows = (
            db.query(RewardSessionModel)
            .options(selectinload(RewardSessionModel.events))
            .where(RewardSessionModel.account_id == account_id)
            .order_by(RewardSessionModel.created_at.asc())
            .all()
        )

        cloud_sessions = []
        for s in cloud_rows:
            events = []
            for ev in sorted(s.events, key=lambda e: e.id or 0):
                events.append({
                    "event": ev.event,
                    "timestamp": ev.timestamp.isoformat() if ev.timestamp else "",
                    "total_points": int(ev.total_points),
                    "total_quizzes": int(ev.total_quizzes),
                    "total_correct": int(ev.total_correct),
                    "total_answered": int(ev.total_answered),
                    "note": decrypt_text(user_dek, ev.note) if user_dek is not None else ev.note,
                })
            cloud_sessions.append({
                "session_id": s.session_id,
                "events": events,
                "updated_at": s.updated_at,
            })

        local_sessions = []
        if account_dir.exists():
            for path in sorted(account_dir.glob("reward_session_*.jsonl")):
                session_id = path.stem
                try:
                    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                    events = [json.loads(line) for line in lines]
                except Exception:
                    events = []
                import os as _os
                file_mtime = datetime.fromtimestamp(_os.path.getmtime(path), tz=UTC)
                local_sessions.append({
                    "session_id": session_id,
                    "events": events,
                    "updated_at": file_mtime,
                })

        merged = _merge_reward_sessions(local_sessions, cloud_sessions)

        if not local_writable:
            return len(merged)

        desired_files: set[Path] = set()
        for sess in merged:
            out_path = account_dir / f"{sess['session_id']}.jsonl"
            lines: list[str] = []
            for event in sess["events"]:
                lines.append(
                    json.dumps(
                        {
                            "event": event.get("event", "snapshot"),
                            "timestamp": event.get("timestamp", ""),
                            "total_points": int(event.get("total_points", 0)),
                            "total_quizzes": int(event.get("total_quizzes", 0)),
                            "total_correct": int(event.get("total_correct", 0)),
                            "total_answered": int(event.get("total_answered", 0)),
                            "note": event.get("note", ""),
                        },
                        ensure_ascii=True,
                    )
                )
            try:
                out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            except Exception as exc:
                if not _is_read_only_fs_error(exc):
                    raise
                return len(merged)
            desired_files.add(out_path.resolve())

        deleted = 0
        for path in sorted(account_dir.glob("reward_session_*.jsonl"), reverse=True):
            if path.resolve() in desired_files:
                continue
            try:
                path.unlink()
            except Exception as exc:
                if not _is_read_only_fs_error(exc):
                    raise
                return len(merged)
            deleted += 1
        return len(merged)


def push_account_to_cloud(account_id: str, dek: bytes, *, delete_stale: bool = False) -> SyncReport:
    if is_stateless_db_mode():
        raise RuntimeError("push_account_to_cloud is only valid in SQLite sync mode")

    normalized = normalize_account_id(account_id)
    report = SyncReport(account_id=normalized)
    now = datetime.now(UTC)

    report.chat_sessions = _push_chat_sessions_to_cloud(normalized, dek)
    report.reward_sessions = _push_reward_sessions_to_cloud(normalized, dek)

    if is_sqlite_mode():
        report.uploaded_files = _push_artifacts_to_cloud_sqlite(normalized, dek, delete_stale, report, now)
        _push_bin_items_to_cloud_sqlite(normalized)
        _push_registry_to_cloud_sqlite(normalized, dek, report, now)
    else:
        _push_filesystem_to_cloud(normalized, dek, delete_stale, report, now)

    _check_qa_presence(normalized, report)
    return report


def _push_artifacts_to_cloud_sqlite(normalized: str, dek: bytes, delete_stale: bool, report: SyncReport, now: datetime) -> int:
    """Push artifacts from local SQLite (plaintext) to cloud DB (encrypted).

    Content is loaded and encrypted outside the cloud transaction to avoid
    holding the Supabase pooler connection open across crypto operations.
    """
    with _local_session() as local_db:
        local_artifacts = (
            local_db.query(
                AccountArtifactModel.relative_path,
                AccountArtifactModel.sha256,
                AccountArtifactModel.size_bytes,
                AccountArtifactModel.updated_at,
                AccountArtifactModel.deleted_at,
                AccountArtifactModel.expires_at,
                AccountArtifactModel.hydration_status,
            )
            .where(AccountArtifactModel.account_id == normalized)
            .order_by(AccountArtifactModel.relative_path.asc())
            .all()
        )
    with _db_session() as cloud_db:
        cloud_by_path = {
            row.relative_path: row
            for row in (
                cloud_db.query(AccountArtifactModel)
                .options(
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
                .where(AccountArtifactModel.account_id == normalized)
                .all()
            )
        }
    upload_candidates: list[tuple[object, object | None]] = []
    for local_row in local_artifacts:
        cloud_row = cloud_by_path.pop(local_row.relative_path, None)
        if local_row.relative_path == _QA_ARTIFACT_PATH:
            report.qa_artifact_synced = True
        if getattr(local_row, "hydration_status", HYDRATION_FULL) != HYDRATION_FULL:
            continue
        should_upload = cloud_row is None
        if cloud_row is not None:
            cloud_ts = _ensure_aware(cloud_row.updated_at)
            local_ts = _ensure_aware(local_row.updated_at)
            if cloud_ts > local_ts:
                continue
            should_upload = not _same_artifact_metadata(local_row, cloud_row)
        if not should_upload:
            continue
        upload_candidates.append((local_row, cloud_row))

    encrypted_payloads: list[tuple[object, object | None, bytes, int]] = []
    if upload_candidates:
        candidate_paths = [local_row.relative_path for local_row, _ in upload_candidates]
        with _local_session() as content_db:
            content_by_path = {
                row.relative_path: row
                for row in (
                    content_db.query(
                        AccountArtifactModel.relative_path,
                        AccountArtifactModel.content,
                    )
                    .where(
                        AccountArtifactModel.account_id == normalized,
                        AccountArtifactModel.relative_path.in_(candidate_paths),
                    )
                    .all()
                )
            }
        for local_row, cloud_row in upload_candidates:
            content_row = content_by_path.get(local_row.relative_path)
            if content_row is None:
                continue
            plain_content = bytes(content_row.content)
            encrypted_content = encrypt_bytes(dek, plain_content)
            encrypted_payloads.append((local_row, cloud_row, encrypted_content, len(plain_content)))

    with _db_session() as cloud_db:
        for local_row, cloud_row, encrypted_content, plain_size in encrypted_payloads:
            if cloud_row is None:
                cloud_db.add(
                    AccountArtifactModel(
                        account_id=normalized,
                        relative_path=local_row.relative_path,
                        content=encrypted_content,
                        sha256=local_row.sha256,
                        size_bytes=plain_size,
                        updated_at=local_row.updated_at or now,
                        deleted_at=local_row.deleted_at,
                        expires_at=local_row.expires_at,
                        hydration_status=HYDRATION_FULL,
                    )
                )
            else:
                merged = cloud_db.merge(cloud_row)
                merged.content = encrypted_content
                merged.sha256 = local_row.sha256
                merged.size_bytes = plain_size
                merged.updated_at = local_row.updated_at or now
                merged.deleted_at = local_row.deleted_at
                merged.expires_at = local_row.expires_at
                merged.hydration_status = HYDRATION_FULL
            report.uploaded_files += 1
        if delete_stale:
            stale_paths = list(cloud_by_path.keys())
            if stale_paths:
                cloud_db.query(AccountArtifactModel).where(
                    AccountArtifactModel.account_id == normalized,
                    AccountArtifactModel.relative_path.in_(stale_paths),
                ).delete(synchronize_session=False)
                report.deleted_remote_files += len(stale_paths)
        cloud_db.commit()
    return report.uploaded_files


def _copy_bin_item_fields(target: BinItemModel, source: BinItemModel) -> None:
    target.item_type = source.item_type
    target.item_id = source.item_id
    target.name = source.name
    target.metadata_json = source.metadata_json
    target.deleted_at = source.deleted_at
    target.expires_at = source.expires_at


def _push_bin_items_to_cloud_sqlite(normalized: str) -> None:
    """Mirror local SQLite bin rows to cloud so deleted/restored state survives sync."""
    with _local_session() as local_db:
        local_rows = (
            local_db.query(BinItemModel)
            .where(BinItemModel.account_id == normalized)
            .all()
        )
    with _db_session() as cloud_db:
        cloud_by_id = {
            row.id: row
            for row in cloud_db.query(BinItemModel).where(
                BinItemModel.account_id == normalized
            ).all()
        }
        for local_row in local_rows:
            cloud_row = cloud_by_id.pop(local_row.id, None)
            if cloud_row is None:
                cloud_db.add(
                    BinItemModel(
                        id=local_row.id,
                        account_id=normalized,
                        item_type=local_row.item_type,
                        item_id=local_row.item_id,
                        name=local_row.name,
                        metadata_json=local_row.metadata_json,
                        deleted_at=local_row.deleted_at,
                        expires_at=local_row.expires_at,
                    )
                )
            else:
                _copy_bin_item_fields(cloud_row, local_row)
        for stale_cloud_row in cloud_by_id.values():
            cloud_db.delete(stale_cloud_row)
        cloud_db.commit()


def _push_registry_to_cloud_sqlite(normalized: str, dek: bytes, report: SyncReport, now: datetime) -> None:
    """Push account registry from local SQLite to cloud DB."""
    with _local_session() as local_db:
        local_row = local_db.query(AccountRegistryRecordModel).where(
            AccountRegistryRecordModel.account_id == normalized
        ).first()
    if local_row is None:
        return
    record = json.loads(local_row.profile_json)
    if not isinstance(record, dict):
        return
    # Learning state is intentionally plaintext operational data. Documents,
    # chat content, and private profiles remain DEK-protected; lesson/quiz
    # state must not be able to block lesson creation when an unlock session
    # is unavailable or a legacy ciphertext is unreadable.
    record_to_save = dict(record)
    record_json = json.dumps(record_to_save, ensure_ascii=True, indent=2)
    with _db_session() as cloud_db:
        cloud_row = cloud_db.query(AccountRegistryRecordModel).where(
            AccountRegistryRecordModel.account_id == normalized
        ).first()
        if cloud_row is None:
            cloud_db.add(AccountRegistryRecordModel(
                account_id=normalized, profile_json=record_json, updated_at=now,
            ))
        else:
            cloud_row.profile_json = record_json
            cloud_row.updated_at = now
        cloud_db.commit()
    report.registry_synced = True


def _push_filesystem_to_cloud(normalized: str, dek: bytes, delete_stale: bool, report: SyncReport, now: datetime) -> None:
    """Push from local filesystem to cloud DB (original behavior)."""
    record = _load_local_account_record(normalized)
    local_files = _iter_local_files(normalized)
    local_map = {_relative_account_path(normalized, path): path for path in local_files}

    with _db_session() as db:
        artifact_rows = db.query(AccountArtifactModel).where(AccountArtifactModel.account_id == normalized).all()
        artifact_by_path = {row.relative_path: row for row in artifact_rows}
        for relative_path, path in local_map.items():
            payload = path.read_bytes()
            digest = _hash_bytes(payload)
            ciphertext = encrypt_bytes(dek, payload)
            if relative_path == _QA_ARTIFACT_PATH:
                report.qa_artifact_synced = True
            row = artifact_by_path.pop(relative_path, None)
            if row is None:
                db.add(
                    AccountArtifactModel(
                        account_id=normalized,
                        relative_path=relative_path,
                        content=ciphertext,
                        sha256=digest,
                        size_bytes=len(payload),
                        updated_at=now,
                        hydration_status=HYDRATION_FULL,
                    )
                )
            else:
                row.content = ciphertext
                row.sha256 = digest
                row.size_bytes = len(payload)
                row.updated_at = now
                row.hydration_status = HYDRATION_FULL
            report.uploaded_files += 1

        if delete_stale:
            for row in artifact_by_path.values():
                db.delete(row)
                report.deleted_remote_files += 1

        registry = db.query(AccountRegistryRecordModel).where(AccountRegistryRecordModel.account_id == normalized).first()
        # Learning state is plaintext operational data; only sensitive content
        # uses the account DEK.
        record_to_save = dict(record)
        record_json = json.dumps(record_to_save, ensure_ascii=True, indent=2)
        if registry is None:
            db.add(
                AccountRegistryRecordModel(
                    account_id=normalized,
                    profile_json=record_json,
                    updated_at=now,
                )
            )
        else:
            registry.profile_json = record_json
            registry.updated_at = now
        report.registry_synced = True
        db.commit()


def pull_account_from_cloud(account_id: str, dek: bytes, *, content_mode: ContentPullMode = "full") -> SyncReport:
    if is_stateless_db_mode():
        raise RuntimeError("pull_account_from_cloud is only valid in SQLite sync mode")

    normalized = normalize_account_id(account_id)
    report = SyncReport(account_id=normalized)

    if is_sqlite_mode():
        # The registry contains lesson/quiz state. Pull it first so
        # callers never observe stale local ciphertext while artifact hydration
        # is still running.
        _pull_registry_from_cloud_sqlite(normalized, dek, report)
        report.chat_sessions = _pull_chat_sessions_from_cloud(normalized, dek)
        report.reward_sessions = _pull_reward_sessions_from_cloud(normalized, dek)
        report.downloaded_files = _pull_artifacts_from_cloud_sqlite(normalized, dek, report, content_mode=content_mode)
        _pull_bin_items_from_cloud_sqlite(normalized)
    else:
        report.chat_sessions = _pull_chat_sessions_from_cloud(normalized, dek)
        report.reward_sessions = _pull_reward_sessions_from_cloud(normalized, dek)
        _pull_cloud_to_filesystem(normalized, dek, report)

    _check_qa_presence(normalized, report)
    return report


def _pull_artifacts_from_cloud_sqlite(
    normalized: str,
    dek: bytes,
    report: SyncReport,
    *,
    content_mode: ContentPullMode = "full",
) -> int:
    """Pull artifacts from cloud DB (encrypted) to local SQLite (plaintext)."""
    with _db_session() as cloud_db:
        cloud_rows = (
            cloud_db.query(
                AccountArtifactModel.relative_path,
                AccountArtifactModel.sha256,
                AccountArtifactModel.size_bytes,
                AccountArtifactModel.updated_at,
                AccountArtifactModel.deleted_at,
                AccountArtifactModel.expires_at,
                AccountArtifactModel.hydration_status,
            )
            .where(AccountArtifactModel.account_id == normalized)
            .order_by(AccountArtifactModel.relative_path.asc())
            .all()
        )
    with _local_session() as local_db:
        local_by_path = {
            row.relative_path: row
            for row in (
                local_db.query(AccountArtifactModel)
                .options(
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
                .where(AccountArtifactModel.account_id == normalized)
                .all()
            )
        }
        download_candidates = []
        for cloud_row in cloud_rows:
            if cloud_row.relative_path == _QA_ARTIFACT_PATH:
                report.qa_artifact_synced = True
            local_row = local_by_path.pop(cloud_row.relative_path, None)

            should_download = local_row is None
            if local_row is not None:
                local_ts = _ensure_aware(local_row.updated_at)
                cloud_ts = _ensure_aware(cloud_row.updated_at)
                if local_ts > cloud_ts:
                    continue
                should_download = (
                    not _same_artifact_metadata(local_row, cloud_row)
                    or getattr(local_row, "hydration_status", HYDRATION_FULL) != HYDRATION_FULL
                )
            if not should_download:
                continue
            if not _should_hydrate_artifact(cloud_row.relative_path, content_mode):
                if local_row is None:
                    local_db.add(
                        AccountArtifactModel(
                            account_id=normalized,
                            relative_path=cloud_row.relative_path,
                            content=b"",
                            sha256=cloud_row.sha256,
                            size_bytes=cloud_row.size_bytes,
                            updated_at=cloud_row.updated_at,
                            deleted_at=cloud_row.deleted_at,
                            expires_at=cloud_row.expires_at,
                            hydration_status=HYDRATION_METADATA_ONLY,
                        )
                    )
                else:
                    local_row.content = b""
                    local_row.sha256 = cloud_row.sha256
                    local_row.size_bytes = cloud_row.size_bytes
                    local_row.updated_at = cloud_row.updated_at
                    local_row.deleted_at = cloud_row.deleted_at
                    local_row.expires_at = cloud_row.expires_at
                    local_row.hydration_status = HYDRATION_METADATA_ONLY
                continue
            download_candidates.append((cloud_row, local_row))

        candidate_paths = [cloud_row.relative_path for cloud_row, _ in download_candidates]
        if candidate_paths:
            with _db_session() as content_db:
                content_by_path = {
                    row.relative_path: row
                    for row in (
                        content_db.query(
                            AccountArtifactModel.relative_path,
                            AccountArtifactModel.content,
                        )
                        .where(
                            AccountArtifactModel.account_id == normalized,
                            AccountArtifactModel.relative_path.in_(candidate_paths),
                        )
                        .all()
                    )
                }
        else:
            content_by_path = {}

        for cloud_row, local_row in download_candidates:
            content_row = content_by_path.get(cloud_row.relative_path)
            if content_row is None:
                continue
            try:
                plain_content = decrypt_bytes(dek, bytes(content_row.content))
            except Exception as exc:
                logger.warning("Failed decrypting artifact %s: %s", cloud_row.relative_path, exc)
                if local_row is not None:
                    local_row.hydration_status = HYDRATION_FAILED
                continue

            if local_row is None:
                local_db.add(
                    AccountArtifactModel(
                        account_id=normalized,
                        relative_path=cloud_row.relative_path,
                        content=plain_content,
                        sha256=cloud_row.sha256,
                        size_bytes=len(plain_content),
                        updated_at=cloud_row.updated_at,
                        deleted_at=cloud_row.deleted_at,
                        expires_at=cloud_row.expires_at,
                        hydration_status=HYDRATION_FULL,
                    )
                )
            else:
                local_row.content = plain_content
                local_row.sha256 = cloud_row.sha256
                local_row.size_bytes = len(plain_content)
                local_row.updated_at = cloud_row.updated_at
                local_row.deleted_at = cloud_row.deleted_at
                local_row.expires_at = cloud_row.expires_at
                local_row.hydration_status = HYDRATION_FULL
            report.downloaded_files += 1
        local_db.commit()
    return report.downloaded_files


def hydrate_account_artifacts(
    account_id: str,
    dek: bytes,
    *,
    limit: int | None = None,
    paths: list[str] | None = None,
) -> dict:
    """Download/decrypt deferred artifact bodies into the local SQLite cache."""
    if not is_sqlite_mode():
        return {"hydrated": 0, "failed": 0, "remaining": 0}

    normalized = normalize_account_id(account_id)
    with _local_session() as local_db:
        query = local_db.query(AccountArtifactModel).where(
            AccountArtifactModel.account_id == normalized,
            AccountArtifactModel.hydration_status.in_(HYDRATION_PENDING_STATUSES),
        )
        if paths is not None:
            query = query.where(AccountArtifactModel.relative_path.in_(paths))
        rows = (
            query.order_by(AccountArtifactModel.size_bytes.asc(), AccountArtifactModel.updated_at.desc())
            .limit(limit)
            .all()
            if limit is not None
            else query.order_by(AccountArtifactModel.size_bytes.asc(), AccountArtifactModel.updated_at.desc()).all()
        )
        candidate_paths = [row.relative_path for row in rows]

    if not candidate_paths:
        logger.info("Hydration for %s: nothing to hydrate", normalized)
        return {"hydrated": 0, "failed": 0, "remaining": _hydration_remaining_count(normalized)}

    t0 = datetime.now(UTC)
    total_bytes = sum(int(r.size_bytes or 0) for r in rows)
    logger.info("Hydration for %s: starting %d artifacts (%d bytes) with %d workers",
                normalized, len(candidate_paths), total_bytes, _HYDRATION_MAX_WORKERS)

    with _db_session() as cloud_db:
        cloud_rows = (
            cloud_db.query(AccountArtifactModel)
            .where(
                AccountArtifactModel.account_id == normalized,
                AccountArtifactModel.relative_path.in_(candidate_paths),
            )
            .all()
        )
        cloud_by_path: dict[str, tuple[bytes, str, datetime, datetime | None, datetime | None]] = {}
        for row in cloud_rows:
            cloud_by_path[row.relative_path] = (
                bytes(row.content),
                str(row.sha256),
                row.updated_at,
                row.deleted_at,
                row.expires_at,
            )

    hydrated = 0
    failed = 0
    def _decrypt_one(path: str) -> tuple[str, bytes | None, str | None]:
        entry = cloud_by_path.get(path)
        if entry is None:
            return (path, None, "missing from cloud")
        cloud_content, cloud_sha256, _, _, _ = entry
        try:
            plain_content = decrypt_bytes(dek, cloud_content)
            if _hash_bytes(plain_content) != cloud_sha256:
                return (path, None, "hash mismatch")
        except Exception as exc:
            logger.warning("Failed hydrating artifact %s: %s", path, exc)
            return (path, None, str(exc))
        return (path, plain_content, None)

    results: list[tuple[str, bytes | None, str | None]] = []
    with ThreadPoolExecutor(max_workers=_HYDRATION_MAX_WORKERS) as executor:
        future_to_path = {executor.submit(_decrypt_one, path): path for path in candidate_paths}
        for future in as_completed(future_to_path):
            results.append(future.result())

    with _local_session() as local_db:
        local_by_path = {
            row.relative_path: row
            for row in (
                local_db.query(AccountArtifactModel)
                .where(
                    AccountArtifactModel.account_id == normalized,
                    AccountArtifactModel.relative_path.in_(candidate_paths),
                )
                .all()
            )
        }
        for path, plain_content, err in results:
            local_row = local_by_path.get(path)
            if local_row is None or err is not None:
                failed += 1
                if local_row is not None:
                    local_row.hydration_status = HYDRATION_FAILED
                continue

            entry = cloud_by_path[path]
            _, cloud_sha256, cloud_updated_at, cloud_deleted_at, cloud_expires_at = entry
            local_row.content = plain_content
            local_row.sha256 = cloud_sha256
            local_row.size_bytes = len(plain_content)
            local_row.updated_at = cloud_updated_at
            local_row.deleted_at = cloud_deleted_at
            local_row.expires_at = cloud_expires_at
            local_row.hydration_status = HYDRATION_FULL
            hydrated += 1
        local_db.commit()

    elapsed = (datetime.now(UTC) - t0).total_seconds()
    logger.info("Hydration for %s: hydrated=%d failed=%d remaining=%d in %.1fs",
                normalized, hydrated, failed, _hydration_remaining_count(normalized), elapsed)

    return {"hydrated": hydrated, "failed": failed, "remaining": _hydration_remaining_count(normalized)}


def hydrate_account_artifact(account_id: str, dek: bytes, relative_path: str) -> bool:
    result = hydrate_account_artifacts(account_id, dek, paths=[relative_path])
    return int(result.get("hydrated", 0)) > 0


def _hydration_remaining_count(normalized: str) -> int:
    with _local_session() as local_db:
        return (
            local_db.query(AccountArtifactModel)
            .where(
                AccountArtifactModel.account_id == normalized,
                AccountArtifactModel.hydration_status.in_(HYDRATION_PENDING_STATUSES),
            )
            .count()
        )


def get_hydration_status(account_id: str) -> dict:
    normalized = normalize_account_id(account_id)
    with _local_session() as local_db:
        rows = (
            local_db.query(
                AccountArtifactModel.hydration_status,
                AccountArtifactModel.size_bytes,
            )
            .where(AccountArtifactModel.account_id == normalized)
            .all()
        )
    full = 0
    metadata_only = 0
    failed = 0
    remaining_bytes = 0
    for row in rows:
        status = row.hydration_status or HYDRATION_FULL
        if status == HYDRATION_FULL:
            full += 1
        elif status == HYDRATION_FAILED:
            failed += 1
            remaining_bytes += int(row.size_bytes or 0)
        else:
            metadata_only += 1
            remaining_bytes += int(row.size_bytes or 0)
    return {
        "full": full,
        "metadata_only": metadata_only,
        "failed": failed,
        "remaining": metadata_only + failed,
        "remaining_bytes": remaining_bytes,
    }


def _pull_bin_items_from_cloud_sqlite(normalized: str) -> None:
    """Mirror cloud bin rows into local SQLite after artifact soft-delete metadata is pulled."""
    with _db_session() as cloud_db:
        cloud_rows = (
            cloud_db.query(BinItemModel)
            .where(BinItemModel.account_id == normalized)
            .all()
        )
    with _local_session() as local_db:
        local_by_id = {
            row.id: row
            for row in local_db.query(BinItemModel).where(
                BinItemModel.account_id == normalized
            ).all()
        }
        for cloud_row in cloud_rows:
            local_row = local_by_id.pop(cloud_row.id, None)
            if _cloud_bin_item_is_stale_for_local_restore(local_db, normalized, cloud_row):
                if local_row is not None:
                    local_db.delete(local_row)
                continue
            if local_row is None:
                local_db.add(
                    BinItemModel(
                        id=cloud_row.id,
                        account_id=normalized,
                        item_type=cloud_row.item_type,
                        item_id=cloud_row.item_id,
                        name=cloud_row.name,
                        metadata_json=cloud_row.metadata_json,
                        deleted_at=cloud_row.deleted_at,
                        expires_at=cloud_row.expires_at,
                    )
                )
            else:
                _copy_bin_item_fields(local_row, cloud_row)
        for stale_local_row in local_by_id.values():
            local_db.delete(stale_local_row)
        local_db.commit()


def _cloud_bin_item_is_stale_for_local_restore(db, normalized: str, cloud_row: BinItemModel) -> bool:
    if cloud_row.item_type != "document":
        return False
    try:
        metadata = json.loads(cloud_row.metadata_json)
    except Exception:
        return False
    relative_path = metadata.get("relative_path")
    if not relative_path:
        return False
    artifact = db.query(AccountArtifactModel).where(
        AccountArtifactModel.account_id == normalized,
        AccountArtifactModel.relative_path == relative_path,
    ).first()
    if artifact is None or artifact.deleted_at is not None:
        return False
    return _ensure_aware(artifact.updated_at) >= _ensure_aware(cloud_row.deleted_at)


def _pull_registry_from_cloud_sqlite(normalized: str, dek: bytes, report: SyncReport) -> None:
    """Pull account registry from cloud DB to local SQLite."""
    from backend.app.services.crypto_local import decrypt_text as _decrypt_text
    with _db_session() as cloud_db:
        cloud_row = cloud_db.query(AccountRegistryRecordModel).where(
            AccountRegistryRecordModel.account_id == normalized
        ).first()
    if cloud_row is None:
        return
    record = json.loads(cloud_row.profile_json)
    if not isinstance(record, dict):
        return
    qh = record.get("quiz_history")
    if isinstance(qh, str) and qh.startswith("enc:v1:"):
        try:
            record["quiz_history"] = json.loads(_decrypt_text(dek, qh))
        except Exception as exc:
            raise RuntimeError(f"Failed decrypting quiz_history for {normalized}; unlock key does not match account data") from exc
    lp = record.get("lesson_progress")
    if isinstance(lp, str) and lp.startswith("enc:v1:"):
        try:
            record["lesson_progress"] = json.loads(_decrypt_text(dek, lp))
        except Exception as exc:
            raise RuntimeError(f"Failed decrypting lesson_progress for {normalized}; unlock key does not match account data") from exc
    lp = record.get("lesson_progress")
    if isinstance(lp, dict):
        try:
            with _local_session() as local_db:
                local_row = local_db.query(AccountRegistryRecordModel).where(
                    AccountRegistryRecordModel.account_id == normalized
                ).first()
            if local_row is not None:
                local_record = json.loads(local_row.profile_json)
                local_lp = local_record.get("lesson_progress", {})
                if isinstance(local_lp, dict):
                    existing_lessons = local_lp.get("lessons", {})
                    cloud_lessons = lp.get("lessons", {})
                    if isinstance(existing_lessons, dict) and isinstance(cloud_lessons, dict):
                        merged = dict(existing_lessons)
                        for lid, cloud_state in cloud_lessons.items():
                            local_state = merged.get(lid)
                            if local_state is None:
                                merged[lid] = cloud_state
                            else:
                                local_ts = local_state.get("updated_at") or local_state.get("last_updated_at", "")
                                cloud_ts = cloud_state.get("updated_at") or cloud_state.get("last_updated_at", "")
                                if cloud_ts > local_ts:
                                    merged[lid] = cloud_state
                        lp["lessons"] = merged
                    record["lesson_progress"] = lp
        except Exception as exc:
            logger.warning("Failed merging lesson progress for %s: %s", normalized, exc)
    record_json = json.dumps(record, ensure_ascii=True, indent=2)
    with _local_session() as local_db:
        local_row = local_db.query(AccountRegistryRecordModel).where(
            AccountRegistryRecordModel.account_id == normalized
        ).first()
        if local_row is None:
            local_db.add(AccountRegistryRecordModel(
                account_id=normalized, profile_json=record_json,
                updated_at=datetime.now(UTC),
            ))
        else:
            local_row.profile_json = record_json
            local_row.updated_at = datetime.now(UTC)
        local_db.commit()
    report.registry_synced = True


def _pull_cloud_to_filesystem(normalized: str, dek: bytes, report: SyncReport) -> None:
    """Pull from cloud DB to local filesystem (original behavior)."""
    root = _account_root_dir(normalized)

    with _db_session() as db:
        artifact_rows = (
            db.query(AccountArtifactModel)
            .where(AccountArtifactModel.account_id == normalized)
            .order_by(AccountArtifactModel.relative_path.asc())
            .all()
        )
        desired_files: set[Path] = set()
        local_artifacts_writable = True
        for row in artifact_rows:
            target = root / Path(row.relative_path)
            try:
                payload = decrypt_bytes(dek, bytes(row.content))
            except Exception:
                continue
            if row.relative_path == _QA_ARTIFACT_PATH:
                report.qa_artifact_synced = True
            if _hash_bytes(payload) != str(row.sha256):
                raise RuntimeError(f"Artifact hash mismatch for {row.relative_path}")
            # Skip if local file is newer than cloud
            if target.exists():
                try:
                    local_mtime = datetime.fromtimestamp(target.stat().st_mtime, tz=UTC)
                    cloud_ts = _ensure_aware(row.updated_at)
                    if local_mtime > cloud_ts:
                        desired_files.add(target.resolve())
                        continue
                except Exception:
                    pass
            if local_artifacts_writable:
                try:
                    _safe_write_bytes(target, payload)
                    desired_files.add(target.resolve())
                    report.downloaded_files += 1
                except Exception as exc:
                    if _is_read_only_fs_error(exc):
                        local_artifacts_writable = False
                    else:
                        raise

        report.deleted_local_files = 0

        registry = db.query(AccountRegistryRecordModel).where(AccountRegistryRecordModel.account_id == normalized).first()
        if registry is not None:
            record = json.loads(registry.profile_json)
            if not isinstance(record, dict):
                raise RuntimeError("Cloud account registry record is invalid.")
            from backend.app.services.crypto_local import decrypt_text
            qh = record.get("quiz_history")
            if isinstance(qh, str) and qh.startswith("enc:v1:"):
                try:
                    record["quiz_history"] = json.loads(decrypt_text(dek, qh))
                except Exception as exc:
                    logger.warning("Failed decrypting quiz_history for %s: %s", normalized, exc)
            lp = record.get("lesson_progress")
            if isinstance(lp, str) and lp.startswith("enc:v1:"):
                try:
                    record["lesson_progress"] = json.loads(decrypt_text(dek, lp))
                except Exception as exc:
                    logger.warning("Failed decrypting lesson_progress for %s: %s", normalized, exc)
            # Merge lesson progress: keep local lessons not in cloud, update if cloud is newer
            try:
                store = _account_store()
                existing_payload = store._load()
                existing_user = existing_payload.get("users", {}).get(normalized, {})
                existing_lp = existing_user.get("lesson_progress", {})
                cloud_lp = record.get("lesson_progress", {})
                if isinstance(existing_lp, dict) and isinstance(cloud_lp, dict):
                    existing_lessons = existing_lp.get("lessons", {})
                    cloud_lessons = cloud_lp.get("lessons", {})
                    if isinstance(existing_lessons, dict) and isinstance(cloud_lessons, dict):
                        merged = dict(existing_lessons)
                        for lid, cloud_state in cloud_lessons.items():
                            local_state = merged.get(lid)
                            if local_state is None:
                                merged[lid] = cloud_state
                            else:
                                local_ts = local_state.get("updated_at") or local_state.get("last_updated_at", "")
                                cloud_ts = cloud_state.get("updated_at") or cloud_state.get("last_updated_at", "")
                                if cloud_ts > local_ts:
                                    merged[lid] = cloud_state
                        cloud_lp["lessons"] = merged
                    record["lesson_progress"] = cloud_lp
            except Exception as exc:
                logger.warning("Failed merging lesson progress for %s: %s", normalized, exc)
            try:
                _write_local_account_record(normalized, record, dek=dek)
                report.registry_synced = True
            except Exception as exc:
                if not _is_read_only_fs_error(exc):
                    raise

        db.commit()
