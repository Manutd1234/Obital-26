from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.quiz.account_system import AccountStore
from engine.quiz import account_system
from engine.quiz.quiz_backend import QuizGradeResponse


class AccountSystemTests(unittest.TestCase):
    def test_create_user_stores_pbkdf2_fields_and_can_login(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                profile = store.create_user(username="Alice", pin="1234")
                self.assertEqual(profile["username"], "alice")
                self.assertTrue(profile.get("pin_hash"))
                self.assertTrue(profile.get("pin_salt"))
                self.assertEqual(profile.get("pin_iterations"), 200_000)

                ok = store.verify_login(username="ALICE", pin="1234")
                self.assertIsNotNone(ok)
                bad = store.verify_login(username="alice", pin="9999")
                self.assertIsNone(bad)

    def test_verify_login_preserves_other_local_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                store.create_user(username="alice", pin="1234")
                store.create_user(username="bob", pin="4321")

                self.assertIsNone(store.verify_login(username="bob", pin="9999"))
                after_failure = store._load()
                self.assertEqual(set(after_failure["users"]), {"alice", "bob"})
                self.assertEqual(after_failure["users"]["bob"].get("failed_pin_attempts"), 1)

                self.assertIsNotNone(store.verify_login(username="bob", pin="4321"))
                after_success = store._load()
                self.assertEqual(set(after_success["users"]), {"alice", "bob"})
                self.assertEqual(after_success["users"]["bob"].get("failed_pin_attempts"), 0)

    def test_migrate_case_variant_keys_and_merge(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            legacy = {
                "users": {
                    "Alice": {"username": "Alice", "total_points": 10, "total_quizzes": 1, "total_correct": 1, "total_answered": 1},
                    "alice": {"username": "alice", "total_points": 20, "total_quizzes": 3, "total_correct": 2, "total_answered": 3},
                }
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                payload = store._load()
            users = payload["users"]
            self.assertEqual(set(users.keys()), {"alice"})
            self.assertEqual(users["alice"]["total_points"], 20)
            self.assertEqual(users["alice"]["total_quizzes"], 3)

    def test_dashboard_summary_uses_remaining_quiz_history_not_stale_counters(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            payload = {
                "users": {
                    "alice": {
                        "username": "alice",
                        "pin_hash": "x",
                        "total_points": 999,
                        "total_quizzes": 99,
                        "total_correct": 99,
                        "total_answered": 99,
                        "quiz_history": [
                            {
                                "quiz_id": "q2",
                                "submitted_at": "2026-06-25T00:00:00+00:00",
                                "score_percent": 50.0,
                                "points_earned": 15,
                                "correct_answers": 1,
                                "answered_questions": 2,
                            }
                        ],
                    }
                }
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                dashboard = AccountStore(path).build_dashboard_data(username="alice")

            self.assertEqual(dashboard["summary"]["total_points"], 15)
            self.assertEqual(dashboard["summary"]["total_quizzes"], 1)
            self.assertEqual(dashboard["summary"]["total_correct"], 1)
            self.assertEqual(dashboard["summary"]["total_answered"], 2)
            self.assertEqual(dashboard["summary"]["accuracy_percent"], 50.0)

    def test_legacy_pin_hash_login_upgrades_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            # Legacy format: sha256(username::pin), no salt.
            from engine.quiz.account_system import _pin_hash

            legacy_pin_hash = _pin_hash(username="bob", pin="4321")
            payload = {
                "users": {
                    "bob": {
                        "username": "bob",
                        "pin_hash": legacy_pin_hash,
                        "total_points": 0,
                        "total_quizzes": 0,
                        "total_correct": 0,
                        "total_answered": 0,
                    }
                }
            }
            # hash for bob::4321 precomputed
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                profile = store.verify_login(username="BOB", pin="4321")
                self.assertIsNotNone(profile)
                upgraded = store.get_user("bob")
            assert upgraded is not None
            self.assertTrue(upgraded.get("pin_salt"))
            self.assertEqual(upgraded.get("pin_iterations"), 200_000)
            self.assertNotEqual(upgraded.get("pin_hash"), legacy_pin_hash)

    def test_apply_quiz_result_requires_authenticated_account(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                result = QuizGradeResponse(
                    quiz_id="q1",
                    total_questions=2,
                    answered_questions=2,
                    correct_answers=1,
                    score_percent=50.0,
                    results=[],
                )
                with self.assertRaises(RuntimeError):
                    store.apply_quiz_result(username="ghost", result=result)

    def test_dashboard_trend_includes_cumulative_points(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                store.create_user(username="alice", pin="1234")
                for quiz_id, correct, score in [
                    ("q1", 1, 50.0),
                    ("q2", 0, 0.0),
                    ("q3", 2, 100.0),
                ]:
                    store.apply_quiz_result(
                        username="alice",
                        result=QuizGradeResponse(
                            quiz_id=quiz_id,
                            total_questions=2,
                            answered_questions=2,
                            correct_answers=correct,
                            score_percent=score,
                            results=[],
                        ),
                    )

                dashboard = store.build_dashboard_data(username="alice", limit=2)

        self.assertEqual([item["quiz_id"] for item in dashboard["trend"]], ["q2", "q3"])
        self.assertEqual([item["cumulative_points"] for item in dashboard["trend"]], [20, 45])

    def test_lesson_library_list_open_restart(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                store.create_user(username="alice", pin="1234")
                checkpoints = [
                    {"title": "c1", "difficulty": "easy", "num_questions": 2, "pass_threshold_percent": 60.0},
                    {"title": "c2", "difficulty": "medium", "num_questions": 2, "pass_threshold_percent": 70.0},
                ]
                store.start_lesson(
                    username="alice",
                    lesson_id="lesson_trees",
                    title="Lesson: trees",
                    topic="trees",
                    checkpoints=checkpoints,
                )
                lessons = store.list_lessons(username="alice")
                self.assertEqual(len(lessons), 1)
                self.assertEqual(lessons[0]["lesson_id"], "lesson_trees")
                self.assertEqual(lessons[0]["status"], "active")

                store.apply_lesson_attempt(
                    username="alice",
                    lesson_id="lesson_trees",
                    checkpoint_index=0,
                    quiz_id="q1",
                    score_percent=80.0,
                    passed=True,
                )
                store.set_active_lesson(username="alice", lesson_id="lesson_trees")
                active = store.get_active_lesson(username="alice")
                self.assertIsNotNone(active)
                assert active is not None
                self.assertEqual(int(active.get("current_checkpoint_index", -1)), 1)

                restarted = store.restart_lesson(username="alice", lesson_id="lesson_trees")
                self.assertEqual(int(restarted.get("current_checkpoint_index", -1)), 0)
                self.assertEqual(bool(restarted.get("completed", True)), False)
                self.assertEqual(restarted.get("attempts", []), [])


class AccountStoreDbSaveRegressionTests(unittest.TestCase):
    """Test that _save() does not overwrite fresh profile data with stale
    encrypted DB blobs when the DEK is not available."""

    SessionLocal = None
    memory_engine = None
    _class_set_up = False

    @classmethod
    def setUpClass(cls) -> None:
        import backend.app.services.db
        import backend.app.services.models

        cls._orig_db_url = backend.app.services.db.DATABASE_URL
        cls._orig_effective_url = backend.app.services.db.EFFECTIVE_DATABASE_URL
        cls._orig_db_engine = backend.app.services.db.engine
        cls._orig_db_session = backend.app.services.db.SessionLocal

        cls.memory_engine = create_engine("sqlite:///:memory:", future=True)
        cls.SessionLocal = sessionmaker(bind=cls.memory_engine, autoflush=False, autocommit=False, future=True)

        backend.app.services.db.Base.metadata.create_all(bind=cls.memory_engine)

        backend.app.services.db.DATABASE_URL = "postgresql+psycopg://dummy:dummy@localhost/dummy"
        backend.app.services.db.EFFECTIVE_DATABASE_URL = backend.app.services.db.DATABASE_URL
        backend.app.services.db.engine = cls.memory_engine
        backend.app.services.db.SessionLocal = cls.SessionLocal

        import engine.quiz.account_system as account_system_mod
        import backend.app.services.storage as storage_mod
        cls._orig_ss_db_url = account_system_mod._is_db_storage_mode.__module__
        storage_mod.SessionLocal = cls.SessionLocal
        cls._class_set_up = True

    @classmethod
    def tearDownClass(cls) -> None:
        import backend.app.services.db
        import backend.app.services.storage
        import backend.app.services.models

        if cls._class_set_up:
            backend.app.services.db.DATABASE_URL = cls._orig_db_url
            backend.app.services.db.EFFECTIVE_DATABASE_URL = cls._orig_effective_url
            backend.app.services.db.engine = cls._orig_db_engine
            backend.app.services.db.SessionLocal = cls._orig_db_session
            backend.app.services.storage.SessionLocal = cls._orig_db_session

    def setUp(self) -> None:
        import backend.app.services.models
        with self.SessionLocal() as db:
            db.query(backend.app.services.models.AccountRegistryRecordModel).delete()
            db.query(backend.app.services.models.AccountArtifactModel).delete()
            db.commit()

    def test_start_lesson_uses_explicit_dek_for_target_postgres_account(self) -> None:
        """Background lesson saves must not rely on request context or read other users."""
        import backend.app.services.models
        from backend.app.services.crypto_local import decrypt_text

        alice_dek = b"a" * 32
        bob_dek = b"b" * 32
        alice_profile = {
            "username": "alice",
            "pin_hash": "hash",
            "pin_salt": "salt",
            "lesson_progress": {"active_lesson_id": None, "lessons": {}},
        }
        bob_profile = {
            "username": "bob",
            "pin_hash": "hash",
            "pin_salt": "salt",
            "lesson_progress": {"active_lesson_id": None, "lessons": {}},
        }
        account_system._encrypt_private_profile_fields(alice_profile, dek=alice_dek)
        account_system._encrypt_private_profile_fields(bob_profile, dek=bob_dek)

        with self.SessionLocal() as db:
            db.add_all([
                backend.app.services.models.AccountRegistryRecordModel(
                    account_id="alice",
                    profile_json=json.dumps(alice_profile),
                ),
                backend.app.services.models.AccountRegistryRecordModel(
                    account_id="bob",
                    profile_json=json.dumps(bob_profile),
                ),
            ])
            db.commit()

        with tempfile.TemporaryDirectory() as td:
            store = AccountStore(Path(td) / "accounts.json")
            state = store.start_lesson(
                username="alice",
                lesson_id="lesson_graphs",
                title="Graphs",
                topic="graphs",
                checkpoints=[],
                user_dek=alice_dek,
            )

        self.assertEqual(state["lesson_id"], "lesson_graphs")
        with self.SessionLocal() as db:
            alice_row = db.get(backend.app.services.models.AccountRegistryRecordModel, "alice")
            bob_row = db.get(backend.app.services.models.AccountRegistryRecordModel, "bob")
            self.assertIsNotNone(alice_row)
            self.assertIsNotNone(bob_row)
            alice_saved = json.loads(alice_row.profile_json)
            bob_saved = json.loads(bob_row.profile_json)

        alice_lessons = alice_saved["lesson_progress"]
        self.assertIn("lesson_graphs", alice_lessons["lessons"])
        self.assertEqual(bob_saved["lesson_progress"], bob_profile["lesson_progress"])
        self.assertEqual(bob_saved["lesson_progress"], bob_profile["lesson_progress"])

    def test_save_without_dek_does_not_overwrite_fresh_data_with_stale_encrypted_blob(self) -> None:
        """Regression: _save() previously replaced fresh in-memory quiz_history
        with old encrypted DB blobs when the DEK was unavailable."""
        import backend.app.services.models
        from backend.app.api.common import active_user_dek

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")

            store = AccountStore(path)

            # 1) Create user + seed into DB via _load
            store.create_user(username="admin", pin="1234")
            store.create_user(username="admin2", pin="1234")

            # 2) Simulate unlock: set a fake DEK
            dek = b"x" * 32
            token = active_user_dek.set(dek)

            # 3) Take a quiz -> quiz_history is a list in memory
            result = QuizGradeResponse(
                quiz_id="q1",
                total_questions=5,
                answered_questions=5,
                correct_answers=3,
                score_percent=60.0,
                results=[],
            )
            store.apply_quiz_result(username="admin", result=result)
            store.update_user_profile("admin", "prefers concise explanations")
            store.add_friend(username="admin", friend_id="admin2", display_name="Admin Two")
            store.set_active_quiz(username="admin", quiz_state={"quiz_id": "active-q1", "questions": []})
            store.start_lesson(
                username="admin",
                lesson_id="lesson-1",
                title="Lesson One",
                topic="testing",
                checkpoints=[],
                user_dek=dek,
            )

            # 4) Verify the DB row has encrypted private profile fields while
            # leaderboard counters remain plaintext.
            with self.SessionLocal() as db:
                row = db.query(backend.app.services.models.AccountRegistryRecordModel).where(
                    backend.app.services.models.AccountRegistryRecordModel.account_id == "admin"
                ).first()
                self.assertIsNotNone(row)
                profile = json.loads(row.profile_json)
                for field in ("user_profile", "agent_profile", "friends"):
                    self.assertIsInstance(profile[field], str)
                    self.assertTrue(profile[field].startswith("enc:v1:"), field)
                for field in ("quiz_history", "lesson_progress", "active_quiz"):
                    self.assertNotIsInstance(profile[field], str)
                self.assertEqual(profile["total_points"], 35)
                self.assertEqual(profile["total_quizzes"], 1)

            # 5) Simulate lock: clear the DEK
            active_user_dek.reset(token)

            # 6) Take another quiz (DEK is now None, quiz_history updated in memory)
            result2 = QuizGradeResponse(
                quiz_id="q2",
                total_questions=3,
                answered_questions=3,
                correct_answers=2,
                score_percent=66.7,
                results=[],
            )
            store.apply_quiz_result(username="admin", result=result2)

            # 7) Verify the DB now has fresh unencrypted quiz_history (q2 survived)
            with self.SessionLocal() as db:
                row = db.query(backend.app.services.models.AccountRegistryRecordModel).where(
                    backend.app.services.models.AccountRegistryRecordModel.account_id == "admin"
                ).first()
                self.assertIsNotNone(row)
                profile = json.loads(row.profile_json)
                # Must NOT be the stale encrypted blob — must be the fresh list with q2
                self.assertIsInstance(profile["quiz_history"], list)
                self.assertEqual(profile["total_quizzes"], 2)
                self.assertEqual(profile["total_points"], 60)


class PinLockoutTests(unittest.TestCase):
    def test_empty_pin_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                with self.assertRaises(RuntimeError):
                    store.create_user(username="alice", pin="")

    def test_short_pin_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                with self.assertRaises(RuntimeError):
                    store.create_user(username="alice", pin="12")

    def test_non_digit_pin_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                with self.assertRaises(RuntimeError):
                    store.create_user(username="alice", pin="abcd")

    def test_lockout_after_max_failed_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                store.create_user(username="alice", pin="1234")
                for _ in range(account_system._PIN_MAX_FAILED_ATTEMPTS):
                    result = store.verify_login(username="alice", pin="9999")
                    self.assertIsNone(result)
                with self.assertRaises(RuntimeError):
                    store.verify_login(username="alice", pin="1234")

    def test_success_resets_lockout_counter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                store.create_user(username="alice", pin="1234")
                max_attempts = account_system._PIN_MAX_FAILED_ATTEMPTS
                for _ in range(max_attempts - 1):
                    store.verify_login(username="alice", pin="9999")
                profile = store.verify_login(username="alice", pin="1234")
                self.assertIsNotNone(profile)
                data = store._load()
                self.assertEqual(data["users"]["alice"].get("failed_pin_attempts", 0), 0)

    def test_lockout_expires_after_time(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                store.create_user(username="alice", pin="1234")
                for _ in range(account_system._PIN_MAX_FAILED_ATTEMPTS):
                    store.verify_login(username="alice", pin="9999")
                data = store._load()
                data["users"]["alice"]["pin_locked_until"] = "2020-01-01T00:00:00+00:00"
                store._save(data)
                profile = store.verify_login(username="alice", pin="1234")
                self.assertIsNotNone(profile)

    def test_legacy_hash_forced_migration_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            from engine.quiz.account_system import _pin_hash
            legacy_hash = _pin_hash(username="bob", pin="4321")
            payload = {
                "users": {
                    "bob": {
                        "username": "bob",
                        "pin_hash": legacy_hash,
                        "total_points": 0,
                        "total_quizzes": 0,
                        "total_correct": 0,
                        "total_answered": 0,
                    }
                }
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                profile = store.verify_login(username="bob", pin="4321")
                self.assertIsNotNone(profile)
                data = store._load()
                bob = data["users"]["bob"]
                self.assertTrue(bob.get("pin_salt"))
                self.assertEqual(bob.get("pin_iterations"), 200_000)
                self.assertNotEqual(bob["pin_hash"], legacy_hash)

    def test_lockout_recorded_in_profile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "accounts.json"
            with mock.patch.object(account_system, "_is_db_storage_mode", return_value=False):
                store = AccountStore(path)
                store.create_user(username="alice", pin="1234")
                store.verify_login(username="alice", pin="9999")
                data = store._load()
                alice = data["users"]["alice"]
                self.assertEqual(alice.get("failed_pin_attempts"), 1)
                self.assertIsNotNone(alice.get("pin_last_failed_at"))


if __name__ == "__main__":
    unittest.main()
