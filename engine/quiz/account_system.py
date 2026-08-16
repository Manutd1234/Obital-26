from __future__ import annotations

import json
import hashlib
import hmac
import logging
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine.quiz.quiz_backend import QuizBuildResponse, QuizGradeResponse

_PIN_MAX_FAILED_ATTEMPTS = int(os.getenv("PIN_MAX_FAILED_ATTEMPTS", "5"))
_PIN_LOCKOUT_MINUTES = int(os.getenv("PIN_LOCKOUT_MINUTES", "15"))
_PIN_MAX_LOCKOUT_MINUTES = 240
_PIN_MIN_LENGTH = 4
_PIN_MAX_LENGTH = 12
_PRESENCE_ONLINE_WINDOW_SECONDS = int(os.getenv("OBITAL_PRESENCE_ONLINE_WINDOW_SECONDS", "180") or "180")

logger = logging.getLogger(__name__)

_PRIVATE_PROFILE_FIELDS = (
    "user_profile",
    "agent_profile",
    "friends",
)

_LEGACY_ENCRYPTED_LEARNING_FIELDS = ("quiz_history", "lesson_progress", "active_quiz")


class AccountDataKeyMismatch(RuntimeError):
    """Raised when authenticated account ciphertext cannot be opened by its DEK."""


@dataclass
class AccountUpdate:
    username: str
    quiz_points: int
    total_points: int
    total_quizzes: int
    total_correct: int
    total_answered: int


class AccountStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._store_lock = threading.Lock()
        if not _is_db_storage_mode():
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def ensure_user(self, username: str) -> dict:
        username = _normalize_username(username)
        data = self._load()
        users = data.setdefault("users", {})
        profile = users.get(username)
        if profile is None or not str(profile.get("pin_hash", "")).strip():
            raise RuntimeError("User does not exist or has no PIN. Create account first.")
        return profile

    def user_exists(self, username: str) -> bool:
        username = _normalize_username(username)
        if _is_db_storage_mode():
            from backend.app.services.db import SessionLocal
            from backend.app.services.models import AccountRegistryRecordModel

            if SessionLocal is None:
                raise RuntimeError("Database mode is enabled but SessionLocal is unavailable.")
            with SessionLocal() as db:
                return (
                    db.query(AccountRegistryRecordModel.account_id)
                    .where(AccountRegistryRecordModel.account_id == username)
                    .first()
                    is not None
                )
        return self._load_user(username) is not None

    def create_user(self, *, username: str, pin: str) -> dict:
        pin = pin.strip()
        if not pin.isdigit() or len(pin) < _PIN_MIN_LENGTH or len(pin) > _PIN_MAX_LENGTH:
            raise RuntimeError(f"PIN must be {_PIN_MIN_LENGTH}-{_PIN_MAX_LENGTH} digits")
        username = _normalize_username(username)
        with self._store_lock:
            data = self._load()
            users = data.setdefault("users", {})
            if username in users:
                raise RuntimeError("Username already exists.")
            pin_salt = secrets.token_hex(16)
            profile = {
                "username": username,
                "pin_hash": _hash_pin(pin=pin, salt_hex=pin_salt),
                "pin_salt": pin_salt,
                "pin_iterations": 200_000,
                "total_points": 0,
                "total_quizzes": 0,
                "total_correct": 0,
                "total_answered": 0,
                "user_profile": "",
                "agent_profile": "",
                "friends": [],
                "presence_status": "offline",
                "last_seen_at": None,
                "created_at": _utc_now_iso(),
                "updated_at": _utc_now_iso(),
            }
            users[username] = profile
            self._save(data)
            return profile

    def verify_login(self, *, username: str, pin: str) -> dict | None:
        username = _normalize_username(username)
        with self._store_lock:
            data = self._load()
            users = data.setdefault("users", {})
            profile = users.get(username)
            if profile is None:
                return None
            _enforce_pin_lockout(profile)
            if not _verify_pin(profile, username=username, pin=pin):
                _record_pin_failure(profile)
                users[username] = profile
                self._save(data)
                return None
            if not str(profile.get("pin_salt", "")).strip():
                pin_salt = secrets.token_hex(16)
                profile["pin_salt"] = pin_salt
                profile["pin_iterations"] = 200_000
                profile["pin_hash"] = _hash_pin(pin=pin, salt_hex=pin_salt)
                profile["updated_at"] = _utc_now_iso()
            _record_pin_success(profile)
            users[username] = profile
            self._save(data)
            return profile

    def apply_quiz_result(
        self,
        *,
        username: str,
        result: QuizGradeResponse,
        quiz: QuizBuildResponse | None = None,
        user_dek: bytes | None = None,
    ) -> AccountUpdate:
        username = _normalize_username(username)
        with self._store_lock:
            # Load this account through the strict path first.  This performs
            # the legacy learning-field migration before a write can treat an
            # encrypted history string as an invalid value and replace it.
            profile = self._load_user(username, dek=user_dek)
            if profile is None or not str(profile.get("pin_hash", "")).strip():
                raise RuntimeError("Cannot update points: authenticated account not found.")

            quiz_points = _points_for_result(result)
            profile["total_points"] = int(profile.get("total_points", 0)) + quiz_points
            profile["total_quizzes"] = int(profile.get("total_quizzes", 0)) + 1
            profile["total_correct"] = int(profile.get("total_correct", 0)) + int(result.correct_answers)
            profile["total_answered"] = int(profile.get("total_answered", 0)) + int(result.answered_questions)
            history = profile.setdefault("quiz_history", [])
            if not isinstance(history, list):
                history = []
                profile["quiz_history"] = history
            history.append(_build_quiz_history_entry(result=result, quiz=quiz, points_earned=quiz_points))
            if len(history) > 500:
                del history[:-500]
            profile["updated_at"] = _utc_now_iso()
            self._save_user(username, profile, dek=user_dek)

        return AccountUpdate(
            username=username,
            quiz_points=quiz_points,
            total_points=int(profile["total_points"]),
            total_quizzes=int(profile["total_quizzes"]),
            total_correct=int(profile["total_correct"]),
            total_answered=int(profile["total_answered"]),
        )

    def get_user(self, username: str, user_dek: bytes | None = None) -> dict | None:
        username = _normalize_username(username)
        return self._load_user(username, dek=user_dek)

    def get_user_profile(self, username: str) -> str:
        profile = self.get_user(username)
        if profile is None:
            return ""
        return str(profile.get("user_profile", ""))

    def update_user_profile(self, username: str, new_markdown: str) -> None:
        username = _normalize_username(username)
        with self._store_lock:
            profile = self._load_user(username)
            if profile is None:
                raise RuntimeError("User not found.")
            profile["user_profile"] = new_markdown
            profile["updated_at"] = _utc_now_iso()
            self._save_user(username, profile)

    def get_agent_profile(self, username: str) -> str:
        profile = self.get_user(username)
        if profile is None:
            return ""
        return str(profile.get("agent_profile", ""))

    def update_agent_profile(self, username: str, new_markdown: str) -> None:
        username = _normalize_username(username)
        with self._store_lock:
            profile = self._load_user(username)
            if profile is None:
                raise RuntimeError("User not found.")
            profile["agent_profile"] = new_markdown
            profile["updated_at"] = _utc_now_iso()
            self._save_user(username, profile)

    def get_friends(self, *, username: str) -> list[dict]:
        profile = self.get_user(username)
        if profile is None:
            return []
        friends = profile.get("friends", [])
        if not isinstance(friends, list):
            return []
        return friends

    def is_friend(self, username: str, friend_id: str) -> bool:
        friends = self.get_friends(username=username)
        return any(
            isinstance(f, dict) and f.get("account_id") == friend_id
            for f in friends
        )

    def add_friend(self, *, username: str, friend_id: str, display_name: str | None = None) -> dict:
        username = _normalize_username(username)
        friend_id = _normalize_username(friend_id)
        if username == friend_id:
            raise RuntimeError("Cannot add yourself as a friend")
        with self._store_lock:
            profile = self._load_user(username)
            if profile is None:
                raise RuntimeError("User not found.")
            if not self.user_exists(friend_id):
                raise RuntimeError("User not found")
            friends = profile.setdefault("friends", [])
            if not isinstance(friends, list):
                friends = []
                profile["friends"] = friends
            if any(
                isinstance(f, dict) and f.get("account_id") == friend_id
                for f in friends
            ):
                raise RuntimeError("Already friends with this user")
            entry = {
                "account_id": friend_id,
                "display_name": display_name or friend_id,
                "added_at": _utc_now_iso(),
            }
            friends.append(entry)
            profile["updated_at"] = _utc_now_iso()
            self._save_user(username, profile)
            return entry

    def remove_friend(self, *, username: str, friend_id: str) -> None:
        username = _normalize_username(username)
        friend_id = _normalize_username(friend_id)
        with self._store_lock:
            profile = self._load_user(username)
            if profile is None:
                raise RuntimeError("User not found.")
            friends = profile.get("friends", [])
            if not isinstance(friends, list):
                return
            before = len(friends)
            friends[:] = [
                f for f in friends
                if not (isinstance(f, dict) and f.get("account_id") == friend_id)
            ]
            if len(friends) == before:
                raise RuntimeError("Friend not found in your list")
            profile["friends"] = friends
            profile["updated_at"] = _utc_now_iso()
            self._save_user(username, profile)

    def update_presence(self, *, username: str, status: str, last_seen_at: str | None = None) -> None:
        username = _normalize_username(username)
        with self._store_lock:
            profile = self._load_user(username)
            if profile is None:
                return
            profile["presence_status"] = status
            profile["last_seen_at"] = last_seen_at or _utc_now_iso()
            profile["updated_at"] = _utc_now_iso()
            self._save_user(username, profile)

    def get_friends_presence(self, *, username: str) -> list[dict]:
        friends = self.get_friends(username=username)
        result: list[dict] = []
        now = datetime.now(timezone.utc)
        for f in friends:
            fid = f.get("account_id", "") if isinstance(f, dict) else ""
            if not fid:
                continue
            target = self.get_user(fid)
            if target is None:
                continue
            last_seen = target.get("last_seen_at")
            presence_status = target.get("presence_status", "offline")
            online = False
            if last_seen and presence_status == "online":
                try:
                    dt = datetime.fromisoformat(str(last_seen))
                    if (now - dt).total_seconds() < _PRESENCE_ONLINE_WINDOW_SECONDS:
                        online = True
                except (ValueError, TypeError):
                    pass
            result.append({
                "account_id": fid,
                "display_name": f.get("display_name", fid) if isinstance(f, dict) else fid,
                "status": presence_status,
                "online": online,
                "last_seen_at": last_seen,
            })
        return result

    def start_lesson(
        self,
        *,
        username: str,
        lesson_id: str,
        title: str,
        topic: str,
        checkpoints: list[dict],
        user_dek: bytes | None = None,
    ) -> dict:
        username = _normalize_username(username)
        with self._store_lock:
            # Lesson generation saves from a background thread. Request-scoped
            # contextvars are not inherited by that thread, so use the DEK
            # explicitly and touch only this account's Postgres row.
            profile = self._load_user(username, dek=user_dek)
            if profile is None:
                raise RuntimeError("User not found.")
            lesson_progress = profile.setdefault("lesson_progress", {"active_lesson_id": None, "lessons": {}})
            if not isinstance(lesson_progress, dict):
                raise RuntimeError(
                    f"Cannot start lesson for {username}: lesson_progress is not a dict. "
                    "The account data may still be encrypted — ensure the DEK is available in the current thread context."
                )
            lessons = lesson_progress.setdefault("lessons", {})
            if not isinstance(lessons, dict):
                lessons = {}
                lesson_progress["lessons"] = lessons
            state = {
                "lesson_id": lesson_id,
                "title": title,
                "topic": topic,
                "current_checkpoint_index": 0,
                "completed": False,
                "checkpoints": checkpoints,
                "materials": [],
                "attempts": [],
                "created_at": _utc_now_iso(),
                "last_accessed_at": _utc_now_iso(),
                "updated_at": _utc_now_iso(),
            }
            lessons[lesson_id] = state
            lesson_progress["active_lesson_id"] = lesson_id
            profile["updated_at"] = _utc_now_iso()
            self._save_user(username, profile, dek=user_dek)
            return state

    def add_lesson_material(
        self,
        *,
        username: str,
        lesson_id: str,
        material: dict,
        user_dek: bytes | None = None,
    ) -> dict:
        """Append one idempotent chat/quiz resource without changing checkpoint progress."""
        username = _normalize_username(username)
        source_key = str(material.get("source_key", "")).strip()
        material_id = str(material.get("material_id", "")).strip()
        if not source_key or not material_id:
            raise RuntimeError("Invalid lesson material.")

        with self._store_lock:
            profile = self._load_user(username, dek=user_dek)
            if profile is None:
                raise RuntimeError("User not found.")
            lesson_progress = profile.setdefault(
                "lesson_progress",
                {"active_lesson_id": None, "lessons": {}},
            )
            if not isinstance(lesson_progress, dict):
                raise RuntimeError("Lesson data is unavailable. Unlock the workspace and retry.")
            lessons = lesson_progress.setdefault("lessons", {})
            if not isinstance(lessons, dict):
                raise RuntimeError("Lesson data is invalid.")
            state = lessons.get(lesson_id)
            if not isinstance(state, dict):
                raise RuntimeError("Lesson not found.")

            materials = state.setdefault("materials", [])
            if not isinstance(materials, list):
                materials = []
                state["materials"] = materials
            for existing in materials:
                if isinstance(existing, dict) and existing.get("source_key") == source_key:
                    return {
                        "lesson_id": lesson_id,
                        "material": existing,
                        "created": False,
                        "total_materials": len(materials),
                    }
            if len(materials) >= 50:
                raise RuntimeError("This lesson already has the maximum of 50 saved items.")

            saved = dict(material)
            projected_size = len(
                json.dumps([*materials, saved], ensure_ascii=True).encode("utf-8")
            )
            if projected_size > 2_000_000:
                raise RuntimeError("Saved lesson materials cannot exceed 2 MB.")
            materials.append(saved)
            now = _utc_now_iso()
            state["updated_at"] = now
            state["last_accessed_at"] = now
            lessons[lesson_id] = state
            lesson_progress["lessons"] = lessons
            profile["lesson_progress"] = lesson_progress
            profile["updated_at"] = now
            self._save_user(username, profile, dek=user_dek)
            return {
                "lesson_id": lesson_id,
                "material": saved,
                "created": True,
                "total_materials": len(materials),
            }

    def get_active_lesson(self, *, username: str) -> dict | None:
        profile = self.get_user(username)
        if profile is None:
            return None
        lesson_progress = profile.get("lesson_progress", {})
        if not isinstance(lesson_progress, dict):
            return None
        active_lesson_id = lesson_progress.get("active_lesson_id")
        lessons = lesson_progress.get("lessons", {})
        if not isinstance(lessons, dict):
            return None
        if not active_lesson_id:
            return None
        state = lessons.get(str(active_lesson_id))
        return state if isinstance(state, dict) else None

    def set_active_quiz(self, *, username: str, quiz_state: dict) -> None:
        username = _normalize_username(username)
        with self._store_lock:
            profile = self._load_user(username)
            if profile is None:
                raise RuntimeError("User not found.")
            profile["active_quiz"] = quiz_state
            profile["updated_at"] = _utc_now_iso()
            self._save_user(username, profile)

    def get_active_quiz(self, *, username: str) -> dict | None:
        profile = self.get_user(username)
        if profile is None:
            return None
        quiz = profile.get("active_quiz")
        return quiz if isinstance(quiz, dict) and quiz.get("quiz_id") else None

    def clear_active_quiz(self, *, username: str) -> None:
        username = _normalize_username(username)
        with self._store_lock:
            profile = self._load_user(username)
            if profile is None:
                return
            profile["active_quiz"] = None
            profile["updated_at"] = _utc_now_iso()
            self._save_user(username, profile)

    def set_active_lesson(self, *, username: str, lesson_id: str) -> dict:
        username = _normalize_username(username)
        with self._store_lock:
            profile = self._load_user(username)
            if profile is None:
                raise RuntimeError("User not found.")
            lesson_progress = profile.setdefault("lesson_progress", {"active_lesson_id": None, "lessons": {}})
            lessons = lesson_progress.setdefault("lessons", {})
            state = lessons.get(lesson_id)
            if not isinstance(state, dict):
                raise RuntimeError("Lesson not found.")
            lesson_progress["active_lesson_id"] = lesson_id
            state["last_accessed_at"] = _utc_now_iso()
            state["updated_at"] = _utc_now_iso()
            profile["updated_at"] = _utc_now_iso()
            lessons[lesson_id] = state
            lesson_progress["lessons"] = lessons
            profile["lesson_progress"] = lesson_progress
            self._save_user(username, profile)
            return state

    def restart_lesson(self, *, username: str, lesson_id: str, user_dek: bytes | None = None) -> dict:
        username = _normalize_username(username)
        with self._store_lock:
            profile = self._load_user(username, dek=user_dek)
            if profile is None:
                raise RuntimeError("User not found.")
            lesson_progress = profile.setdefault("lesson_progress", {"active_lesson_id": None, "lessons": {}})
            lessons = lesson_progress.setdefault("lessons", {})
            state = lessons.get(lesson_id)
            if not isinstance(state, dict):
                raise RuntimeError("Lesson not found.")
            state["current_checkpoint_index"] = 0
            state["completed"] = False
            state["attempts"] = []
            state["last_accessed_at"] = _utc_now_iso()
            state["updated_at"] = _utc_now_iso()
            lesson_progress["active_lesson_id"] = lesson_id
            profile["updated_at"] = _utc_now_iso()
            lessons[lesson_id] = state
            lesson_progress["lessons"] = lessons
            profile["lesson_progress"] = lesson_progress
            self._save_user(username, profile, dek=user_dek)
            return state

    def list_lessons(self, *, username: str, user_dek: bytes | None = None) -> list[dict]:
        progress = self.get_lesson_progress(username=username, user_dek=user_dek)
        active = progress.get("active_lesson_id")
        lessons = progress.get("lessons", {})
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

    def get_lesson_progress(self, *, username: str, user_dek: bytes | None = None) -> dict:
        profile = self.get_user(username, user_dek=user_dek)
        if profile is None:
            return {"active_lesson_id": None, "lessons": {}}
        lesson_progress = profile.get("lesson_progress", {"active_lesson_id": None, "lessons": {}})
        if not isinstance(lesson_progress, dict):
            return {"active_lesson_id": None, "lessons": {}}
        lessons = lesson_progress.get("lessons", {})
        if not isinstance(lessons, dict):
            lessons = {}
        return {"active_lesson_id": lesson_progress.get("active_lesson_id"), "lessons": lessons}

    def apply_lesson_attempt(
        self,
        *,
        username: str,
        lesson_id: str,
        checkpoint_index: int,
        quiz_id: str,
        score_percent: float,
        passed: bool,
        user_dek: bytes | None = None,
    ) -> dict:
        username = _normalize_username(username)
        with self._store_lock:
            profile = self._load_user(username, dek=user_dek)
            if profile is None:
                raise RuntimeError("User not found.")
            lesson_progress = profile.setdefault("lesson_progress", {"active_lesson_id": None, "lessons": {}})
            lessons = lesson_progress.setdefault("lessons", {})
            state = lessons.get(lesson_id)
            if not isinstance(state, dict):
                raise RuntimeError("Lesson not found.")
            attempts = state.setdefault("attempts", [])
            if not isinstance(attempts, list):
                attempts = []
                state["attempts"] = attempts
            attempts.append(
                {
                    "checkpoint_index": int(checkpoint_index),
                    "quiz_id": quiz_id,
                    "score_percent": float(score_percent),
                    "passed": bool(passed),
                    "submitted_at": _utc_now_iso(),
                }
            )
            current_index = int(state.get("current_checkpoint_index", 0))
            if passed and int(checkpoint_index) == current_index:
                next_index = current_index + 1
                state["current_checkpoint_index"] = next_index
                checkpoints = state.get("checkpoints", [])
                if isinstance(checkpoints, list) and next_index >= len(checkpoints):
                    state["completed"] = True
            state["updated_at"] = _utc_now_iso()
            state["last_accessed_at"] = _utc_now_iso()
            profile["updated_at"] = _utc_now_iso()
            lessons[lesson_id] = state
            lesson_progress["lessons"] = lessons
            profile["lesson_progress"] = lesson_progress
            self._save_user(username, profile, dek=user_dek)
            return state

    def delete_lesson(self, *, username: str, lesson_id: str) -> dict:
        """Soft-delete a lesson by removing it from the user's lessons and returning its state."""
        username = _normalize_username(username)
        with self._store_lock:
            profile = self._load_user(username)
            if profile is None:
                raise RuntimeError("User not found.")
            lesson_progress = profile.setdefault("lesson_progress", {"active_lesson_id": None, "lessons": {}})
            lessons = lesson_progress.setdefault("lessons", {})
            state = lessons.get(lesson_id)
            if not isinstance(state, dict):
                raise RuntimeError("Lesson not found.")
            del lessons[lesson_id]
            if lesson_progress.get("active_lesson_id") == lesson_id:
                lesson_progress["active_lesson_id"] = None
            profile["updated_at"] = _utc_now_iso()
            self._save_user(username, profile)
            return state

    def restore_lesson(self, *, username: str, lesson_state: dict) -> dict:
        """Restore a previously deleted lesson from its saved state."""
        username = _normalize_username(username)
        with self._store_lock:
            profile = self._load_user(username)
            if profile is None:
                raise RuntimeError("User not found.")
            lesson_progress = profile.setdefault("lesson_progress", {"active_lesson_id": None, "lessons": {}})
            lessons = lesson_progress.setdefault("lessons", {})
            lesson_id = str(lesson_state.get("lesson_id", ""))
            if not lesson_id:
                raise RuntimeError("Invalid lesson state.")
            state = {
                "lesson_id": lesson_id,
                "title": lesson_state.get("title", lesson_id),
                "topic": lesson_state.get("topic", ""),
                "current_checkpoint_index": int(lesson_state.get("current_checkpoint_index", 0)),
                "completed": bool(lesson_state.get("completed", False)),
                "checkpoints": lesson_state.get("checkpoints", []),
                "materials": lesson_state.get("materials", []),
                "attempts": lesson_state.get("attempts", []),
                "created_at": lesson_state.get("created_at", _utc_now_iso()),
                "last_accessed_at": _utc_now_iso(),
                "updated_at": _utc_now_iso(),
            }
            lessons[lesson_id] = state
            profile["updated_at"] = _utc_now_iso()
            self._save_user(username, profile)
            return state

    def build_dashboard_data(
        self, *, username: str, limit: int = 20, user_dek: bytes | None = None
    ) -> dict:
        profile = self._load_user(_normalize_username(username), dek=user_dek)
        if profile is None:
            raise RuntimeError("User not found.")
        history = profile.get("quiz_history", [])
        if not isinstance(history, list):
            history = []
        total_points = sum(int(item.get("points_earned", 0)) for item in history if isinstance(item, dict))
        total_quizzes = len([item for item in history if isinstance(item, dict)])
        total_correct = sum(int(item.get("correct_answers", 0)) for item in history if isinstance(item, dict))
        total_answered = sum(int(item.get("answered_questions", 0)) for item in history if isinstance(item, dict))
        accuracy = round((total_correct / total_answered) * 100.0, 2) if total_answered else 0.0
        trend_all = []
        cumulative_points = 0
        for idx, item in enumerate(history, start=1):
            if not isinstance(item, dict):
                continue
            cumulative_points += int(item.get("points_earned", 0))
            trend_all.append((idx, item, cumulative_points))

        recent = trend_all[-max(1, int(limit)) :]
        trend = []
        for visible_idx, (_history_idx, item, cumulative_points) in enumerate(recent, start=1):
            trend.append(
                {
                    "x": visible_idx,
                    "quiz_id": str(item.get("quiz_id", "")),
                    "submitted_at": str(item.get("submitted_at", "")),
                    "score_percent": float(item.get("score_percent", 0.0)),
                    "points_earned": int(item.get("points_earned", 0)),
                    "cumulative_points": int(cumulative_points),
                    "topic_filter": item.get("topic_filter"),
                    "difficulty_filter": item.get("difficulty_filter"),
                }
            )
        return {
            "username": profile.get("username", username),
            "summary": {
                "total_points": total_points,
                "total_quizzes": total_quizzes,
                "total_correct": total_correct,
                "total_answered": total_answered,
                "accuracy_percent": accuracy,
                "updated_at": profile.get("updated_at"),
            },
            "trend": trend,
        }

    def reconcile_account_counters_from_history(
        self, *, username: str, user_dek: bytes | None = None
    ) -> dict[str, int] | None:
        """Keep leaderboard counters aligned with the dashboard's quiz history totals.

        The dashboard derives these values from ``quiz_history`` while the global
        leaderboard reads cached counters. Rebuild the cached counters for the
        authenticated account before a leaderboard request so a stale counter
        cannot produce a stale rank.
        """
        username = _normalize_username(username)
        with self._store_lock:
            # Never hydrate the whole registry with the requesting account's
            # DEK.  Apart from warning spam, that pattern could write a
            # partially decrypted snapshot over unrelated accounts.
            profile = self._load_user(username, dek=user_dek)
            if profile is None:
                return None

            history = profile.get("quiz_history", [])
            if not isinstance(history, list):
                history = []
            entries = [item for item in history if isinstance(item, dict)]
            totals = {
                "total_points": sum(int(item.get("points_earned", 0)) for item in entries),
                "total_quizzes": len(entries),
                "total_correct": sum(int(item.get("correct_answers", 0)) for item in entries),
                "total_answered": sum(int(item.get("answered_questions", 0)) for item in entries),
            }
            changed = any(int(profile.get(key, 0) or 0) != value for key, value in totals.items())
            if changed:
                profile.update(totals)
                profile["updated_at"] = _utc_now_iso()
                self._save_user(username, profile, dek=user_dek)
            return totals

    def list_leaderboard(self) -> list[dict]:
        """Return ALL accounts ranked by XP (``total_points``), highest first.

        Reads the **plaintext** cached counters (``total_points`` / ``total_quizzes`` /
        ``total_correct`` / ``total_answered``) straight from each account's profile. These
        are maintained by ``apply_quiz_result`` and are the XP signal readable across
        accounts. Learning history is operational state and is also kept plaintext; sensitive
        documents, chats, and private profiles remain DEK-protected.

        Returns the full ranked list (ranks assigned over every account); callers slice to a
        display limit while still being able to locate any account by rank.
        """

        def _entry(account_id: str, profile: dict) -> dict:
            total_correct = int(profile.get("total_correct", 0) or 0)
            total_answered = int(profile.get("total_answered", 0) or 0)
            accuracy = round((total_correct / total_answered) * 100.0, 2) if total_answered else 0.0
            username = str(profile.get("username", account_id) or account_id)
            return {
                "account_id": str(account_id),
                "display_name": username,
                "total_points": int(profile.get("total_points", 0) or 0),
                "total_quizzes": int(profile.get("total_quizzes", 0) or 0),
                "accuracy_percent": accuracy,
            }

        entries: list[dict] = []
        if _is_db_storage_mode():
            from backend.app.services.db import SessionLocal
            from backend.app.services.models import AccountRegistryRecordModel
            if SessionLocal is None:
                raise RuntimeError("Database mode is enabled but SessionLocal is unavailable.")
            with SessionLocal() as db:
                rows = db.query(AccountRegistryRecordModel).all()
                for row in rows:
                    try:
                        profile = json.loads(row.profile_json)
                    except Exception:
                        continue
                    if isinstance(profile, dict):
                        entries.append(_entry(str(row.account_id), profile))
        else:
            if self.path.exists():
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                    users = payload.get("users", {}) if isinstance(payload, dict) else {}
                except Exception:
                    users = {}
                for account_id, profile in users.items():
                    if isinstance(profile, dict):
                        entries.append(_entry(str(account_id), profile))

        # Deterministic ranking: points desc, then quizzes desc, then account_id asc.
        entries.sort(key=lambda e: (-e["total_points"], -e["total_quizzes"], e["account_id"]))
        for idx, entry in enumerate(entries, start=1):
            entry["rank"] = idx
        return entries

    def _load_user(self, username: str, dek: bytes | None = None) -> dict | None:
        if _is_db_storage_mode():
            from backend.app.services.db import SessionLocal, is_sqlite_mode
            from backend.app.services.models import AccountRegistryRecordModel
            if SessionLocal is None:
                raise RuntimeError("Database mode is enabled but SessionLocal is unavailable.")

            if dek is None and not is_sqlite_mode():
                try:
                    from backend.app.api.common import active_user_dek
                    dek = active_user_dek.get()
                except Exception:
                    pass

            with SessionLocal() as db:
                row = (
                    db.query(AccountRegistryRecordModel)
                    .where(AccountRegistryRecordModel.account_id == username)
                    .with_for_update()
                    .first()
                )
                if row is not None:
                    try:
                        stored_profile = json.loads(row.profile_json)
                        if isinstance(stored_profile, dict):
                            profile = dict(stored_profile)
                            if not is_sqlite_mode():
                                _decrypt_private_profile_fields(profile, dek=dek, account_id=username)
                                migrated = _decrypt_legacy_learning_fields(
                                    profile, dek=dek, account_id=username
                                )
                                if migrated:
                                    # Persist only learning fields. Private
                                    # fields remain encrypted at rest.
                                    migrated_profile = dict(stored_profile)
                                    for field in _LEGACY_ENCRYPTED_LEARNING_FIELDS:
                                        migrated_profile[field] = profile[field]
                                    row.profile_json = json.dumps(migrated_profile, ensure_ascii=True)
                                    row.updated_at = datetime.now(timezone.utc)
                                    db.commit()
                            return profile
                    except AccountDataKeyMismatch:
                        raise
                    except Exception:
                        pass
                # Not found in DB — try seeding from local accounts.json
                data = self._load()
                return data.get("users", {}).get(username)

        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            users = payload.get("users", {}) if isinstance(payload, dict) else {}
            return users.get(username)
        except Exception:
            return None

    def _load(self, dek: bytes | None = None) -> dict:
        if _is_db_storage_mode():
            from backend.app.services.db import SessionLocal, is_sqlite_mode
            from backend.app.services.models import AccountRegistryRecordModel
            if SessionLocal is None:
                raise RuntimeError("Database mode is enabled but SessionLocal is unavailable.")

            # Request-scoped contextvars are not inherited by background
            # threads. Prefer an explicitly supplied DEK, while retaining the
            # context fallback for existing synchronous callers.
            if dek is None and not is_sqlite_mode():
                try:
                    from backend.app.api.common import active_user_dek
                    dek = active_user_dek.get()
                except Exception:
                    pass

            with SessionLocal() as db:
                rows = db.query(AccountRegistryRecordModel).all()
                users = {}
                for row in rows:
                    try:
                        profile = json.loads(row.profile_json)
                        if isinstance(profile, dict):
                            if not is_sqlite_mode():
                                _decrypt_private_profile_fields(profile, dek=dek, account_id=str(row.account_id))
                            users[row.account_id] = profile
                    except Exception as exc:
                        import traceback
                        print(f"[AccountStore _load] Error parsing registry row: {exc}")
                        traceback.print_exc()

                # Seeding fallback to bootstrap/seed missing local users in the database
                if self.path.exists():
                    try:
                        payload = json.loads(self.path.read_text(encoding="utf-8"))
                        migrated, changed = self._migrate_payload(payload)
                        local_users = migrated.get("users", {})
                        db_changed = False
                        for username, profile in local_users.items():
                            if username not in users:
                                profile_to_save = dict(profile)
                                if not is_sqlite_mode() and dek is not None:
                                    _encrypt_private_profile_fields(profile_to_save, dek=dek)
                                
                                profile_str = json.dumps(profile_to_save, ensure_ascii=True)
                                row = AccountRegistryRecordModel(
                                    account_id=username,
                                    profile_json=profile_str,
                                    updated_at=datetime.now(timezone.utc)
                                )
                                db.add(row)
                                users[username] = profile
                                db_changed = True
                        if db_changed:
                            db.commit()
                    except Exception as exc:
                        import traceback
                        print(f"[AccountStore _load] Seeding fallback failed: {exc}")
                        traceback.print_exc()
                return {"users": users}

        if not self.path.exists():
            return {"users": {}}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        migrated, changed = self._migrate_payload(payload)
        if changed:
            self._save(migrated)
        return migrated

    def _save_user(self, username: str, profile: dict, dek: bytes | None = None) -> None:
        if _is_db_storage_mode():
            from backend.app.services.db import SessionLocal, is_sqlite_mode
            from backend.app.services.models import AccountRegistryRecordModel
            if SessionLocal is None:
                raise RuntimeError("Database mode is enabled but SessionLocal is unavailable.")

            if dek is None and not is_sqlite_mode():
                try:
                    from backend.app.api.common import active_user_dek
                    dek = active_user_dek.get()
                except Exception:
                    pass

            profile_to_save = dict(profile)

            if not is_sqlite_mode():
                with SessionLocal() as db:
                    row = db.query(AccountRegistryRecordModel).where(
                        AccountRegistryRecordModel.account_id == username
                    ).first()
                if dek is not None:
                    _encrypt_private_profile_fields(profile_to_save, dek=dek)
                else:
                    if row is not None:
                        try:
                            old_profile = json.loads(row.profile_json)
                            if isinstance(old_profile, dict):
                                _preserve_encrypted_private_fields(profile_to_save, old_profile)
                        except Exception:
                            pass

            profile_str = json.dumps(profile_to_save, ensure_ascii=True)
            with SessionLocal() as db:
                row = db.query(AccountRegistryRecordModel).where(
                    AccountRegistryRecordModel.account_id == username
                ).first()
                if row is None:
                    row = AccountRegistryRecordModel(
                        account_id=username,
                        profile_json=profile_str,
                        updated_at=datetime.now(timezone.utc),
                    )
                    db.add(row)
                else:
                    row.profile_json = profile_str
                    row.updated_at = datetime.now(timezone.utc)
                db.commit()
            return

        data = self._load()
        data.setdefault("users", {})[username] = profile
        self._save(data)

    def _save(self, payload: dict, dek: bytes | None = None) -> None:
        if _is_db_storage_mode():
            import logging
            from backend.app.services.db import SessionLocal, is_sqlite_mode
            from backend.app.services.models import AccountRegistryRecordModel
            if SessionLocal is None:
                raise RuntimeError("Database mode is enabled but SessionLocal is unavailable.")
            _log = logging.getLogger(__name__)

            if dek is None and not is_sqlite_mode():
                try:
                    from backend.app.api.common import active_user_dek
                    dek = active_user_dek.get()
                except Exception:
                    pass
                if dek is None:
                    _log.warning("AccountStore._save: no DEK available in non-SQLite mode; sensitive fields will not be encrypted")

            users = payload.get("users", {})
            with SessionLocal() as db:
                for username, profile in users.items():
                    row = db.query(AccountRegistryRecordModel).where(AccountRegistryRecordModel.account_id == username).first()

                    profile_to_save = dict(profile)

                    if not is_sqlite_mode():
                        if dek is not None:
                            _encrypt_private_profile_fields(profile_to_save, dek=dek)
                        else:
                            if row is not None:
                                try:
                                    old_profile = json.loads(row.profile_json)
                                    if isinstance(old_profile, dict):
                                        _preserve_encrypted_private_fields(profile_to_save, old_profile)
                                except Exception:
                                    pass

                    profile_str = json.dumps(profile_to_save, ensure_ascii=True)
                    if row is None:
                        row = AccountRegistryRecordModel(
                            account_id=username,
                            profile_json=profile_str,
                            updated_at=datetime.now(timezone.utc)
                        )
                        db.add(row)
                    else:
                        row.profile_json = profile_str
                        row.updated_at = datetime.now(timezone.utc)
                db.commit()
            return

        import json as json_mod
        import os
        import sys
        import tempfile

        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self.path.parent), suffix=".tmp", prefix="account_"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=True, indent=2)
            os.replace(tmp_path, str(self.path))
            if sys.platform != "win32":
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


    def _migrate_payload(self, payload: dict) -> tuple[dict, bool]:
        users = payload.get("users")
        if not isinstance(users, dict):
            return {"users": {}}, True
        changed = False
        merged: dict[str, dict] = {}
        for raw_key, raw_profile in users.items():
            profile = raw_profile if isinstance(raw_profile, dict) else {}
            normalized = _normalize_username(str(raw_key))
            if not normalized:
                changed = True
                continue
            entry = dict(profile)
            entry["username"] = normalized
            # Ensure accounting fields always exist.
            entry["total_points"] = int(entry.get("total_points", 0))
            entry["total_quizzes"] = int(entry.get("total_quizzes", 0))
            entry["total_correct"] = int(entry.get("total_correct", 0))
            entry["total_answered"] = int(entry.get("total_answered", 0))
            entry.setdefault("user_profile", "")
            entry.setdefault("agent_profile", "")
            entry.setdefault("friends", [])
            entry.setdefault("presence_status", "offline")
            entry.setdefault("last_seen_at", None)
            lesson_progress = entry.get("lesson_progress", {"active_lesson_id": None, "lessons": {}})
            if isinstance(lesson_progress, dict):
                lessons = lesson_progress.get("lessons", {})
                if not isinstance(lessons, dict):
                    lessons = {}
                lesson_progress["lessons"] = lessons
                lesson_progress.setdefault("active_lesson_id", None)
                for lesson_key, lesson_state in lessons.items():
                    if not isinstance(lesson_state, dict):
                        continue
                    lesson_state.setdefault("lesson_id", lesson_key)
                    lesson_state.setdefault("created_at", _utc_now_iso())
                    lesson_state.setdefault("last_accessed_at", lesson_state.get("updated_at", _utc_now_iso()))
                    lesson_state.setdefault("attempts", [])
                    lesson_state.setdefault("checkpoints", [])
                    lesson_state.setdefault("materials", [])
                    lesson_state.setdefault("current_checkpoint_index", 0)
                    lesson_state.setdefault("completed", False)
            entry["lesson_progress"] = lesson_progress
            entry.setdefault("active_quiz", None)
            quiz_history = entry.get("quiz_history", [])
            if not isinstance(quiz_history, list):
                quiz_history = []
            entry["quiz_history"] = quiz_history
            entry.setdefault("created_at", _utc_now_iso())
            entry.setdefault("updated_at", _utc_now_iso())

            existing = merged.get(normalized)
            if existing is None:
                merged[normalized] = entry
            else:
                # Merge duplicate case-variants by keeping max counters.
                existing["total_points"] = max(int(existing.get("total_points", 0)), int(entry.get("total_points", 0)))
                existing["total_quizzes"] = max(
                    int(existing.get("total_quizzes", 0)), int(entry.get("total_quizzes", 0))
                )
                existing["total_correct"] = max(
                    int(existing.get("total_correct", 0)), int(entry.get("total_correct", 0))
                )
                existing["total_answered"] = max(
                    int(existing.get("total_answered", 0)), int(entry.get("total_answered", 0))
                )
                existing_history = existing.get("quiz_history", [])
                if not isinstance(existing_history, list):
                    existing_history = []
                entry_history = entry.get("quiz_history", [])
                if isinstance(entry_history, list):
                    existing_history.extend(entry_history)
                    if len(existing_history) > 500:
                        del existing_history[:-500]
                existing["quiz_history"] = existing_history
                if not str(existing.get("pin_hash", "")).strip() and str(entry.get("pin_hash", "")).strip():
                    existing["pin_hash"] = entry["pin_hash"]
                if not str(existing.get("pin_salt", "")).strip() and str(entry.get("pin_salt", "")).strip():
                    existing["pin_salt"] = entry["pin_salt"]
                existing["updated_at"] = _utc_now_iso()
            if str(raw_key) != normalized:
                changed = True
        if users != merged:
            changed = True
        return {"users": merged}, changed


def _points_for_result(result: QuizGradeResponse) -> int:
    # Simple points model:
    # - 10 points per correct answer
    # - 5 completion bonus when all questions were answered
    completion_bonus = 5 if result.answered_questions == result.total_questions else 0
    return int(result.correct_answers) * 10 + completion_bonus


def _build_quiz_history_entry(
    *,
    result: QuizGradeResponse,
    quiz: QuizBuildResponse | None,
    points_earned: int,
) -> dict:
    return {
        "quiz_id": result.quiz_id,
        "submitted_at": _utc_now_iso(),
        "topic_filter": quiz.topic_filter if quiz is not None else None,
        "difficulty_filter": quiz.difficulty_filter if quiz is not None else None,
        "question_mode": quiz.questions[0].question_mode if quiz is not None and quiz.questions else None,
        "total_questions": int(result.total_questions),
        "answered_questions": int(result.answered_questions),
        "correct_answers": int(result.correct_answers),
        "score_percent": float(result.score_percent),
        "points_earned": int(points_earned),
    }


def _private_field_fallback(field: str) -> object:
    if field == "quiz_history":
        return []
    if field == "lesson_progress":
        return {"active_lesson_id": None, "lessons": {}}
    if field == "active_quiz":
        return None
    if field in {"user_profile", "agent_profile"}:
        return ""
    if field == "friends":
        return []
    return None


def _encrypt_private_profile_fields(profile: dict, *, dek: bytes) -> None:
    from backend.app.services.crypto_local import encrypt_text

    for field in _PRIVATE_PROFILE_FIELDS:
        if field not in profile:
            continue
        value = profile[field]
        if isinstance(value, str) and value.startswith("enc:v1:"):
            continue
        profile[field] = encrypt_text(dek, json.dumps(value))


def _decrypt_private_profile_fields(profile: dict, *, dek: bytes | None, account_id: str) -> None:
    for field in _PRIVATE_PROFILE_FIELDS:
        _decrypt_json_field(
            profile=profile,
            field=field,
            dek=dek,
            fallback=_private_field_fallback(field),
            account_id=account_id,
        )


def _decrypt_legacy_learning_fields(profile: dict, *, dek: bytes | None, account_id: str) -> bool:
    """Decrypt pre-2026 learning fields and lazily migrate them to plaintext."""
    if dek is None:
        return False
    from backend.app.services.crypto_local import decrypt_text_strict

    migrated = False
    for field in _LEGACY_ENCRYPTED_LEARNING_FIELDS:
        value = profile.get(field)
        if not isinstance(value, str) or not value.startswith("enc:v1:"):
            continue
        try:
            profile[field] = json.loads(
                decrypt_text_strict(dek, value, field_name=f"{account_id}.{field}")
            )
        except Exception as exc:
            raise AccountDataKeyMismatch(
                f"ACCOUNT_KEY_MISMATCH: unable to decrypt {field} for {account_id}; unlock again"
            ) from exc
        migrated = True
    return migrated


def _preserve_encrypted_private_fields(profile_to_save: dict, old_profile: dict) -> None:
    for field in _PRIVATE_PROFILE_FIELDS:
        old_val = old_profile.get(field)
        if isinstance(old_val, str) and old_val.startswith("enc:v1:"):
            profile_to_save[field] = old_val


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decrypt_json_field(
    *,
    profile: dict,
    field: str,
    dek: bytes | None,
    fallback: object,
    account_id: str,
) -> None:
    value = profile.get(field)
    if not isinstance(value, str) or not value.startswith("enc:v1:"):
        return
    if dek is None:
        profile[field] = fallback
        return
    from backend.app.services.crypto_local import decrypt_text_strict

    try:
        plaintext = decrypt_text_strict(dek, value, field_name=f"{account_id}.{field}")
        profile[field] = json.loads(plaintext)
    except Exception as exc:
        raise AccountDataKeyMismatch(
            f"ACCOUNT_KEY_MISMATCH: unable to decrypt {field} for {account_id}; unlock again"
        ) from exc


def _pin_hash(*, username: str, pin: str) -> str:
    # Backward-compat helper kept for legacy verification if needed.
    salted = f"{_normalize_username(username)}::{pin}"
    return hashlib.sha256(salted.encode("utf-8")).hexdigest()


def _normalize_username(username: str) -> str:
    return " ".join((username or "").strip().split()).lower()


def _hash_pin(*, pin: str, salt_hex: str, iterations: int = 200_000) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt_hex), iterations)
    return dk.hex()


def _verify_pin(profile: dict, *, username: str, pin: str) -> bool:
    # New format.
    pin_hash = str(profile.get("pin_hash", "")).strip()
    pin_salt = str(profile.get("pin_salt", "")).strip()
    pin_iter = int(profile.get("pin_iterations", 200_000))
    if pin_hash and pin_salt:
        computed = _hash_pin(pin=pin, salt_hex=pin_salt, iterations=pin_iter)
        return hmac.compare_digest(computed, pin_hash)
    # Legacy fallback.
    if pin_hash:
        legacy = _pin_hash(username=username, pin=pin)
        return hmac.compare_digest(legacy, pin_hash)
    return False


def _enforce_pin_lockout(profile: dict) -> None:
    locked_until = profile.get("pin_locked_until")
    if not locked_until:
        return
    try:
        lock_dt = datetime.fromisoformat(locked_until)
        if datetime.now(timezone.utc) < lock_dt:
            raise RuntimeError("Account temporarily locked due to too many failed PIN attempts")
    except (ValueError, TypeError):
        pass


def _record_pin_failure(profile: dict) -> None:
    now = datetime.now(timezone.utc)
    attempts = int(profile.get("failed_pin_attempts", 0)) + 1
    profile["failed_pin_attempts"] = attempts
    profile["pin_last_failed_at"] = now.isoformat()
    if attempts >= _PIN_MAX_FAILED_ATTEMPTS:
        consecutive = int(profile.get("pin_consecutive_lockouts", 0))
        backoff = min(_PIN_LOCKOUT_MINUTES * (2 ** consecutive), _PIN_MAX_LOCKOUT_MINUTES)
        profile["pin_locked_until"] = (now + timedelta(minutes=backoff)).isoformat()
        profile["failed_pin_attempts"] = 0
        profile["pin_consecutive_lockouts"] = consecutive + 1


def _record_pin_success(profile: dict) -> None:
    profile["failed_pin_attempts"] = 0
    profile["pin_locked_until"] = None
    profile["pin_last_failed_at"] = None
    profile["pin_consecutive_lockouts"] = 0


def _is_db_storage_mode() -> bool:
    try:
        from backend.app.services.db import is_db_mode
        return is_db_mode()
    except Exception:
        return False
