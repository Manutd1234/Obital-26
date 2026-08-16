from __future__ import annotations

import base64
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.schemas import ChatMessage, ChatSession
from backend.app.services import account_sync, storage
from backend.app.services.crypto_local import Argon2Params, derive_kek, generate_dek, serialize_params, wrap_dek
from backend.app.services.models import (
    AccountArtifactModel,
    BinItemModel,
    AccountKeyModel,
    AccountRegistryRecordModel,
    Base,
    ChatMessageModel,
    ChatSessionModel,
    RewardEventModel,
    RewardSessionModel,
)


class AccountSyncTests(unittest.TestCase):
    def test_cloud_stateless_mode_rejects_local_cloud_sync_helpers(self) -> None:
        with mock.patch.object(account_sync, "is_stateless_db_mode", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "SQLite sync mode"):
                account_sync.pull_account_from_cloud("admin", b"d" * 32)
            with self.assertRaisesRegex(RuntimeError, "SQLite sync mode"):
                account_sync.push_account_to_cloud("admin", b"d" * 32)

    def test_push_and_pull_full_account_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            account_root = base / "engine" / "accounts" / "admin"
            docs_dir = account_root / "documents"
            config_dir = account_root / "config"
            docs_dir.mkdir(parents=True, exist_ok=True)
            config_dir.mkdir(parents=True, exist_ok=True)
            (docs_dir / "note.txt").write_text("hello", encoding="utf-8")
            (config_dir / "settings.json").write_text("{\"theme\": \"light\"}", encoding="utf-8")

            chat_dir = base / "backend" / "data" / "chat"
            rewards_dir = base / "backend" / "data" / "rewards"
            chat_dir.mkdir(parents=True, exist_ok=True)
            (rewards_dir / "admin").mkdir(parents=True, exist_ok=True)

            sessions_path = chat_dir / "sessions.json"
            sessions_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "session-admin-1",
                            "account_id": "admin",
                            "created_at": "2026-01-01T00:00:00+00:00",
                            "messages": [
                                {
                                    "role": "user",
                                    "text": "hello cloud",
                                    "created_at": "2026-01-01T00:00:01+00:00",
                                    "lesson_id": None,
                                    "doc_chunk_ids": ["chunk-1"],
                                }
                            ],
                        },
                        {
                            "id": "session-other-1",
                            "account_id": "other",
                            "created_at": "2026-01-02T00:00:00+00:00",
                            "messages": [],
                        },
                    ],
                    indent=2,
                ),
                encoding="utf-8",
            )

            reward_file = rewards_dir / "admin" / "reward_session_admin_1.jsonl"
            reward_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "event": "start",
                                "timestamp": "2026-01-01T00:00:00+00:00",
                                "total_points": 10,
                                "total_quizzes": 1,
                                "total_correct": 1,
                                "total_answered": 1,
                                "note": "",
                            }
                        ),
                        json.dumps(
                            {
                                "event": "snapshot",
                                "timestamp": "2026-01-01T00:01:00+00:00",
                                "total_points": 15,
                                "total_quizzes": 2,
                                "total_correct": 2,
                                "total_answered": 2,
                                "note": "updated",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            accounts_path = base / "engine" / "accounts" / "accounts.json"
            accounts_path.parent.mkdir(parents=True, exist_ok=True)
            accounts_path.write_text(
                json.dumps({"users": {"admin": {"username": "admin", "pin_hash": "abc", "pin_salt": "def"}}}),
                encoding="utf-8",
            )

            engine = create_engine(f"sqlite:///{base / 'cloud.sqlite'}", future=True)
            try:
                Base.metadata.create_all(bind=engine)
                Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

                params = Argon2Params()
                salt = b"0123456789abcdef"
                kek = derive_kek("sync-pass", salt, params)
                dek = generate_dek()
                now = datetime.now(UTC)

                with Session() as db:
                    db.add(
                        AccountKeyModel(
                            account_id="admin",
                            salt_b64=base64.b64encode(salt).decode("ascii"),
                            argon2_params_json=serialize_params(params),
                            wrapped_dek_b64=wrap_dek(kek, dek),
                        )
                    )
                    db.add(
                        AccountArtifactModel(
                            account_id="admin",
                            relative_path="documents/stale.txt",
                            content=b"old",
                            sha256="f" * 64,
                            size_bytes=3,
                            updated_at=now,
                        )
                    )
                    db.add(
                        ChatSessionModel(
                            id="session-admin-stale",
                            account_id="admin",
                            created_at=datetime(2025, 1, 1, tzinfo=UTC),
                        )
                    )
                    db.add(
                        ChatMessageModel(
                            session_id="session-admin-stale",
                            seq_no=1,
                            role="user",
                            text="stale",
                            created_at=datetime(2025, 1, 1, tzinfo=UTC),
                            lesson_id=None,
                            doc_chunk_ids_json="[]",
                        )
                    )
                    db.add(
                        RewardSessionModel(
                            session_id="reward_session_admin_stale",
                            account_id="admin",
                            created_at=datetime(2025, 1, 1, tzinfo=UTC),
                        )
                    )
                    db.add(
                        RewardEventModel(
                            session_id="reward_session_admin_stale",
                            event="start",
                            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
                            total_points=1,
                            total_quizzes=1,
                            total_correct=1,
                            total_answered=1,
                            note="stale",
                        )
                    )
                    db.commit()

                with mock.patch.object(account_sync, "BASE_DIR", base), \
                    mock.patch.object(account_sync, "ACCOUNTS_PATH", accounts_path), \
                    mock.patch.object(account_sync, "CHAT_SESSIONS_PATH", sessions_path), \
                    mock.patch.object(account_sync, "REWARDS_DIR", rewards_dir), \
                    mock.patch.object(account_sync, "SessionLocal", Session), \
                    mock.patch.object(account_sync, "db_enabled", return_value=True):
                    push_report = account_sync.push_account_to_cloud("admin", dek)

                    with Session() as db:
                        artifacts = (
                            db.query(AccountArtifactModel)
                            .where(AccountArtifactModel.account_id == "admin")
                            .order_by(AccountArtifactModel.relative_path.asc())
                            .all()
                        )
                        chat_sessions = (
                            db.query(ChatSessionModel)
                            .where(ChatSessionModel.account_id == "admin")
                            .order_by(ChatSessionModel.created_at.asc())
                            .all()
                        )
                        chat_messages = (
                            db.query(ChatMessageModel)
                            .where(ChatMessageModel.session_id == "session-admin-1")
                            .order_by(ChatMessageModel.seq_no.asc())
                            .all()
                        )
                        reward_sessions = (
                            db.query(RewardSessionModel)
                            .where(RewardSessionModel.account_id == "admin")
                            .order_by(RewardSessionModel.created_at.asc())
                            .all()
                        )
                        reward_events = (
                            db.query(RewardEventModel)
                            .where(RewardEventModel.session_id == "reward_session_admin_1")
                            .order_by(RewardEventModel.id.asc())
                            .all()
                        )
                        registry = (
                            db.query(AccountRegistryRecordModel)
                            .where(AccountRegistryRecordModel.account_id == "admin")
                            .first()
                        )

                    self.assertEqual(push_report.chat_sessions, 1)
                    self.assertEqual(push_report.reward_sessions, 1)
                    self.assertEqual(push_report.uploaded_files, 2)
                    self.assertEqual(push_report.deleted_remote_files, 0)
                    self.assertTrue(push_report.registry_synced)
                    self.assertEqual([row.relative_path for row in artifacts], ["config/settings.json", "documents/note.txt", "documents/stale.txt"])
                    self.assertIsNotNone(registry)
                    self.assertNotEqual(artifacts[0].content, b"{\"theme\": \"light\"}")
                    self.assertEqual(account_sync.decrypt_bytes(dek, artifacts[0].content), b"{\"theme\": \"light\"}")
                    self.assertNotEqual(artifacts[1].content, b"hello")
                    self.assertEqual(len(chat_sessions), 2)
                    admin_msgs = [m for m in chat_messages if m.session_id == "session-admin-1"]
                    self.assertEqual(len(admin_msgs), 1)
                    self.assertTrue(admin_msgs[0].text.startswith("enc:v1:"))
                    self.assertEqual(len(reward_sessions), 2)
                    admin_reward_events = [e for e in reward_events if e.session_id == "reward_session_admin_1"]
                    self.assertEqual(len(admin_reward_events), 2)
                    self.assertTrue(admin_reward_events[1].note.startswith("enc:v1:"))

                    (docs_dir / "note.txt").write_text("mutated", encoding="utf-8")
                    (account_root / "documents" / "stale_local.txt").write_text("remove me", encoding="utf-8")
                    sessions_path.write_text(
                        json.dumps(
                            [
                                {
                                    "id": "other-only",
                                    "account_id": "other",
                                    "created_at": "2026-01-03T00:00:00+00:00",
                                    "messages": [],
                                }
                            ],
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    (rewards_dir / "admin" / "reward_session_stale.jsonl").write_text("stale\n", encoding="utf-8")
                    pull_report = account_sync.pull_account_from_cloud("admin", dek)

                self.assertEqual(pull_report.chat_sessions, 2)
                self.assertEqual(pull_report.reward_sessions, 3)
                self.assertEqual(pull_report.downloaded_files, 2)
                self.assertEqual(pull_report.deleted_local_files, 0)
                self.assertTrue((docs_dir / "note.txt").exists())
                self.assertEqual((docs_dir / "note.txt").read_text(encoding="utf-8"), "hello")
                self.assertTrue((account_root / "documents" / "stale_local.txt").exists())
                registry_payload = json.loads(accounts_path.read_text(encoding="utf-8"))
                self.assertIn("admin", registry_payload["users"])
                local_sessions = json.loads(sessions_path.read_text(encoding="utf-8"))
                local_account_ids = [s.get("account_id") for s in local_sessions if s.get("account_id") == "admin"]
                self.assertEqual(len(local_account_ids), 2)
                admin_session = next(s for s in local_sessions if s.get("id") == "session-admin-1")
                self.assertEqual(admin_session["messages"][0]["text"], "hello cloud")
                reward_text = (rewards_dir / "admin" / "reward_session_admin_1.jsonl").read_text(encoding="utf-8")
                self.assertIn('"note": "updated"', reward_text)
            finally:
                engine.dispose()

    def test_pull_with_no_remote_artifacts_preserves_local_account_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            account_root = base / "engine" / "accounts" / "admin"
            docs_dir = account_root / "documents"
            docs_dir.mkdir(parents=True, exist_ok=True)
            local_only = docs_dir / "local-only.txt"
            local_only.write_text("keep me", encoding="utf-8")

            accounts_path = base / "engine" / "accounts" / "accounts.json"
            accounts_path.parent.mkdir(parents=True, exist_ok=True)
            accounts_path.write_text(
                json.dumps({"users": {"admin": {"username": "admin", "pin_hash": "abc", "pin_salt": "def"}}}),
                encoding="utf-8",
            )

            chat_dir = base / "backend" / "data" / "chat"
            rewards_dir = base / "backend" / "data" / "rewards"
            chat_dir.mkdir(parents=True, exist_ok=True)
            (rewards_dir / "admin").mkdir(parents=True, exist_ok=True)
            sessions_path = chat_dir / "sessions.json"
            sessions_path.write_text("[]", encoding="utf-8")

            engine = create_engine(f"sqlite:///{base / 'cloud.sqlite'}", future=True)
            try:
                Base.metadata.create_all(bind=engine)
                Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

                params = Argon2Params()
                salt = b"0123456789abcdef"
                kek = derive_kek("sync-pass", salt, params)
                dek = generate_dek()

                with Session() as db:
                    db.add(
                        AccountKeyModel(
                            account_id="admin",
                            salt_b64=base64.b64encode(salt).decode("ascii"),
                            argon2_params_json=serialize_params(params),
                            wrapped_dek_b64=wrap_dek(kek, dek),
                        )
                    )
                    db.commit()

                with mock.patch.object(account_sync, "BASE_DIR", base), \
                    mock.patch.object(account_sync, "ACCOUNTS_PATH", accounts_path), \
                    mock.patch.object(account_sync, "CHAT_SESSIONS_PATH", sessions_path), \
                    mock.patch.object(account_sync, "REWARDS_DIR", rewards_dir), \
                    mock.patch.object(account_sync, "SessionLocal", Session), \
                    mock.patch.object(account_sync, "db_enabled", return_value=True):
                    report = account_sync.pull_account_from_cloud("admin", dek)

                self.assertEqual(report.downloaded_files, 0)
                self.assertEqual(report.deleted_local_files, 0)
                self.assertTrue(local_only.exists())
                self.assertEqual(local_only.read_text(encoding="utf-8"), "keep me")
            finally:
                engine.dispose()

    def test_pull_chat_with_wrong_dek_does_not_overwrite_local_chat(self) -> None:
        local_engine = create_engine("sqlite://", future=True)
        cloud_engine = create_engine("sqlite://", future=True)
        try:
            Base.metadata.create_all(bind=local_engine)
            Base.metadata.create_all(bind=cloud_engine)
            LocalSession = sessionmaker(bind=local_engine, future=True)
            CloudSession = sessionmaker(bind=cloud_engine, future=True)

            good_dek = b"g" * 32
            wrong_dek = b"w" * 32
            with LocalSession() as db:
                db.add(ChatSessionModel(
                    id="s1",
                    account_id="admin",
                    title="local title",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                ))
                db.add(ChatMessageModel(
                    session_id="s1",
                    seq_no=1,
                    role="user",
                    text="local message",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    doc_chunk_ids_json="[]",
                ))
                db.commit()

            with CloudSession() as db:
                db.add(ChatSessionModel(
                    id="s1",
                    account_id="admin",
                    title=account_sync.encrypt_text(good_dek, "cloud title"),
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 1, 2, tzinfo=UTC),
                ))
                db.add(ChatMessageModel(
                    session_id="s1",
                    seq_no=1,
                    role="user",
                    text=account_sync.encrypt_text(good_dek, "cloud message"),
                    created_at=datetime(2026, 1, 2, tzinfo=UTC),
                    doc_chunk_ids_json="[]",
                ))
                db.commit()

            with mock.patch.object(account_sync, "_local_session", side_effect=lambda: LocalSession()), \
                mock.patch.object(account_sync, "_db_session", side_effect=lambda: CloudSession()), \
                mock.patch.object(account_sync, "is_sqlite_mode", return_value=True):
                with self.assertRaises(RuntimeError):
                    account_sync._pull_chat_sessions_from_cloud("admin", wrong_dek)

            with LocalSession() as db:
                session = db.query(ChatSessionModel).where(ChatSessionModel.id == "s1").one()
                message = db.query(ChatMessageModel).where(ChatMessageModel.session_id == "s1").one()

            self.assertEqual(session.title, "local title")
            self.assertEqual(message.text, "local message")
        finally:
            local_engine.dispose()
            cloud_engine.dispose()


class MergeChatSessionsTests(unittest.TestCase):
    """Tests for _merge_chat_sessions conflict resolution."""

    def test_local_only_session_preserved(self) -> None:
        from backend.app.services.account_sync import _merge_chat_sessions
        local = [ChatSession(id="s1", created_at=datetime(2026, 1, 1, tzinfo=UTC), messages=[])]
        merged = _merge_chat_sessions(local, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].id, "s1")

    def test_cloud_only_session_added(self) -> None:
        from backend.app.services.account_sync import _merge_chat_sessions
        cloud = [ChatSession(id="s1", created_at=datetime(2026, 1, 1, tzinfo=UTC), messages=[])]
        merged = _merge_chat_sessions([], cloud)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].id, "s1")

    def test_newer_cloud_overwrites_local(self) -> None:
        from backend.app.services.account_sync import _merge_chat_sessions
        local = [ChatSession(
            id="s1", created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
            messages=[ChatMessage(role="user", text="old", created_at=datetime(2026, 1, 1, tzinfo=UTC))],
        )]
        cloud = [ChatSession(
            id="s1", created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 2, tzinfo=UTC),
            messages=[ChatMessage(role="user", text="new", created_at=datetime(2026, 1, 2, tzinfo=UTC))],
        )]
        merged = _merge_chat_sessions(local, cloud)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].messages[0].text, "new")

    def test_newer_local_overwrites_cloud(self) -> None:
        from backend.app.services.account_sync import _merge_chat_sessions
        local = [ChatSession(
            id="s1", created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 3, tzinfo=UTC),
            messages=[ChatMessage(role="user", text="local-wins", created_at=datetime(2026, 1, 3, tzinfo=UTC))],
        )]
        cloud = [ChatSession(
            id="s1", created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 2, tzinfo=UTC),
            messages=[ChatMessage(role="user", text="cloud-loses", created_at=datetime(2026, 1, 2, tzinfo=UTC))],
        )]
        merged = _merge_chat_sessions(local, cloud)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].messages[0].text, "local-wins")

    def test_messages_merged_by_seq_no(self) -> None:
        from backend.app.services.account_sync import _merge_chat_sessions
        local = [ChatSession(
            id="s1", created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 2, tzinfo=UTC),
            messages=[
                ChatMessage(role="user", text="m1", created_at=datetime(2026, 1, 1, tzinfo=UTC)),
                ChatMessage(role="assistant", text="m2", created_at=datetime(2026, 1, 1, 1, tzinfo=UTC)),
            ],
        )]
        cloud = [ChatSession(
            id="s1", created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 3, tzinfo=UTC),
            messages=[
                ChatMessage(role="assistant", text="m2-cloud", created_at=datetime(2026, 1, 1, 1, tzinfo=UTC)),
                ChatMessage(role="user", text="m3", created_at=datetime(2026, 1, 1, 2, tzinfo=UTC)),
                ChatMessage(role="assistant", text="m4", created_at=datetime(2026, 1, 1, 3, tzinfo=UTC)),
            ],
        )]
        merged = _merge_chat_sessions(local, cloud)
        self.assertEqual(len(merged), 1)
        texts = [m.text for m in merged[0].messages]
        self.assertEqual(texts, ["m2-cloud", "m3", "m4"])


class MergeRewardSessionsTests(unittest.TestCase):
    """Tests for _merge_reward_sessions conflict resolution."""

    def test_newer_cloud_overwrites_local(self) -> None:
        from backend.app.services.account_sync import _merge_reward_sessions
        local = [{"session_id": "r1", "events": [{"event": "start", "timestamp": "2026-01-01T00:00:00+00:00"}],
                  "updated_at": datetime(2026, 1, 1, tzinfo=UTC)}]
        cloud = [{"session_id": "r1", "events": [{"event": "start", "timestamp": "2026-01-01T00:00:00+00:00"},
                                                   {"event": "snapshot", "timestamp": "2026-01-02T00:00:00+00:00"}],
                  "updated_at": datetime(2026, 1, 2, tzinfo=UTC)}]
        merged = _merge_reward_sessions(local, cloud)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["events"]), 2)

    def test_events_union_by_timestamp(self) -> None:
        from backend.app.services.account_sync import _merge_reward_sessions
        local = [{"session_id": "r1", "events": [
            {"event": "start", "timestamp": "2026-01-01T00:00:00+00:00"},
        ], "updated_at": datetime(2026, 1, 3, tzinfo=UTC)}]
        cloud = [{"session_id": "r1", "events": [
            {"event": "start", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event": "snapshot", "timestamp": "2026-01-02T00:00:00+00:00"},
        ], "updated_at": datetime(2026, 1, 2, tzinfo=UTC)}]
        merged = _merge_reward_sessions(local, cloud)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["events"]), 2)


class WriteChatSessionsSkipTests(unittest.TestCase):
    """Tests that _write_chat_sessions_to_db skips unchanged sessions."""

    def test_skip_when_cloud_same_or_newer(self) -> None:
        engine = create_engine("sqlite://", future=True)
        try:
            Base.metadata.create_all(bind=engine)
            Session = sessionmaker(bind=engine, future=True)
            with Session() as db:
                db.add(ChatSessionModel(
                    id="s1", account_id="admin",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 1, 2, tzinfo=UTC),
                ))
                db.add(ChatMessageModel(
                    session_id="s1", seq_no=1, role="user", text="existing",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                ))
                db.commit()

            with Session() as db:
                from backend.app.services.account_sync import _write_chat_sessions_to_db
                count = _write_chat_sessions_to_db(db, "admin", [
                    ChatSession(
                        id="s1", created_at=datetime(2026, 1, 1, tzinfo=UTC),
                        updated_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
                        messages=[ChatMessage(role="user", text="new", created_at=datetime(2026, 1, 1, 12, tzinfo=UTC))],
                    ),
                ], None)

            with Session() as db:
                msgs = db.query(ChatMessageModel).where(ChatMessageModel.session_id == "s1").all()
                self.assertEqual(len(msgs), 1)
                self.assertEqual(msgs[0].text, "existing")
        finally:
            engine.dispose()

    def test_insert_when_local_newer(self) -> None:
        engine = create_engine("sqlite://", future=True)
        try:
            Base.metadata.create_all(bind=engine)
            Session = sessionmaker(bind=engine, future=True)
            with Session() as db:
                db.add(ChatSessionModel(
                    id="s1", account_id="admin",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                ))
                db.commit()

            with Session() as db:
                from backend.app.services.account_sync import _write_chat_sessions_to_db
                count = _write_chat_sessions_to_db(db, "admin", [
                    ChatSession(
                        id="s1", created_at=datetime(2026, 1, 1, tzinfo=UTC),
                        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
                        messages=[ChatMessage(role="user", text="new-msg", created_at=datetime(2026, 1, 2, tzinfo=UTC))],
                    ),
                ], None)

            with Session() as db:
                msgs = db.query(ChatMessageModel).where(ChatMessageModel.session_id == "s1").all()
                self.assertEqual(len(msgs), 1)
                self.assertEqual(msgs[0].text, "new-msg")
        finally:
            engine.dispose()

    def test_diff_insert_new_messages_only(self) -> None:
        engine = create_engine("sqlite://", future=True)
        try:
            Base.metadata.create_all(bind=engine)
            Session = sessionmaker(bind=engine, future=True)
            with Session() as db:
                db.add(ChatSessionModel(
                    id="s1", account_id="admin",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                ))
                db.add(ChatMessageModel(
                    session_id="s1", seq_no=1, role="user", text="existing",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                ))
                db.commit()

            with Session() as db:
                from backend.app.services.account_sync import _write_chat_sessions_to_db
                count = _write_chat_sessions_to_db(db, "admin", [
                    ChatSession(
                        id="s1", created_at=datetime(2026, 1, 1, tzinfo=UTC),
                        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
                        messages=[
                            ChatMessage(role="user", text="existing", created_at=datetime(2026, 1, 1, tzinfo=UTC)),
                            ChatMessage(role="assistant", text="new-msg", created_at=datetime(2026, 1, 2, tzinfo=UTC)),
                        ],
                    ),
                ], None)

            with Session() as db:
                msgs = db.query(ChatMessageModel).where(ChatMessageModel.session_id == "s1").order_by(ChatMessageModel.seq_no).all()
                self.assertEqual(len(msgs), 2)
                self.assertEqual(msgs[0].text, "existing")
                self.assertEqual(msgs[1].text, "new-msg")
        finally:
            engine.dispose()


class SqliteBinSyncTests(unittest.TestCase):
    def test_push_sqlite_artifacts_carries_bin_markers_to_cloud(self) -> None:
        local_engine = create_engine("sqlite://", future=True)
        cloud_engine = create_engine("sqlite://", future=True)
        try:
            Base.metadata.create_all(bind=local_engine)
            Base.metadata.create_all(bind=cloud_engine)
            LocalSession = sessionmaker(bind=local_engine, future=True)
            CloudSession = sessionmaker(bind=cloud_engine, future=True)

            deleted_at = datetime(2026, 6, 1, tzinfo=UTC)
            expires_at = deleted_at + timedelta(days=30)
            updated_at = datetime(2026, 6, 2, tzinfo=UTC)
            with LocalSession() as db:
                db.add(AccountArtifactModel(
                    account_id="admin",
                    relative_path="documents/bin-note.txt",
                    content=b"local plaintext",
                    sha256=account_sync._hash_bytes(b"local plaintext"),
                    size_bytes=len(b"local plaintext"),
                    updated_at=updated_at,
                    deleted_at=deleted_at,
                    expires_at=expires_at,
                ))
                db.add(BinItemModel(
                    id="bin-1",
                    account_id="admin",
                    item_type="document",
                    item_id="doc-1",
                    name="bin-note.txt",
                    metadata_json=json.dumps({"relative_path": "documents/bin-note.txt"}),
                    deleted_at=deleted_at,
                    expires_at=expires_at,
                ))
                db.commit()

            report = account_sync.SyncReport(account_id="admin")
            dek = b"d" * 32
            with mock.patch.object(account_sync, "_local_session", side_effect=lambda: LocalSession()), \
                mock.patch.object(account_sync, "_db_session", side_effect=lambda: CloudSession()):
                account_sync._push_artifacts_to_cloud_sqlite("admin", dek, False, report, updated_at)
                account_sync._push_bin_items_to_cloud_sqlite("admin")

            with CloudSession() as db:
                artifact = db.query(AccountArtifactModel).where(
                    AccountArtifactModel.account_id == "admin",
                    AccountArtifactModel.relative_path == "documents/bin-note.txt",
                ).one()
                bin_item = db.query(BinItemModel).where(BinItemModel.id == "bin-1").one()
                self.assertEqual(account_sync.decrypt_bytes(dek, artifact.content), b"local plaintext")
                self.assertEqual(account_sync._ensure_aware(artifact.deleted_at), deleted_at)
                self.assertEqual(account_sync._ensure_aware(artifact.expires_at), expires_at)
                self.assertEqual(bin_item.metadata_json, json.dumps({"relative_path": "documents/bin-note.txt"}))
        finally:
            local_engine.dispose()
            cloud_engine.dispose()

    def test_pull_sqlite_artifacts_carries_bin_markers_to_local(self) -> None:
        local_engine = create_engine("sqlite://", future=True)
        cloud_engine = create_engine("sqlite://", future=True)
        try:
            Base.metadata.create_all(bind=local_engine)
            Base.metadata.create_all(bind=cloud_engine)
            LocalSession = sessionmaker(bind=local_engine, future=True)
            CloudSession = sessionmaker(bind=cloud_engine, future=True)

            dek = b"d" * 32
            deleted_at = datetime(2026, 6, 1, tzinfo=UTC)
            expires_at = deleted_at + timedelta(days=30)
            updated_at = datetime(2026, 6, 2, tzinfo=UTC)
            with CloudSession() as db:
                db.add(AccountArtifactModel(
                    account_id="admin",
                    relative_path="documents/cloud-bin-note.txt",
                    content=account_sync.encrypt_bytes(dek, b"cloud plaintext"),
                    sha256=account_sync._hash_bytes(b"cloud plaintext"),
                    size_bytes=len(b"cloud plaintext"),
                    updated_at=updated_at,
                    deleted_at=deleted_at,
                    expires_at=expires_at,
                ))
                db.add(BinItemModel(
                    id="bin-cloud-1",
                    account_id="admin",
                    item_type="document",
                    item_id="doc-cloud-1",
                    name="cloud-bin-note.txt",
                    metadata_json=json.dumps({"relative_path": "documents/cloud-bin-note.txt"}),
                    deleted_at=deleted_at,
                    expires_at=expires_at,
                ))
                db.commit()

            report = account_sync.SyncReport(account_id="admin")
            with mock.patch.object(account_sync, "_local_session", side_effect=lambda: LocalSession()), \
                mock.patch.object(account_sync, "_db_session", side_effect=lambda: CloudSession()):
                account_sync._pull_artifacts_from_cloud_sqlite("admin", dek, report)
                account_sync._pull_bin_items_from_cloud_sqlite("admin")

            with LocalSession() as db:
                artifact = db.query(AccountArtifactModel).where(
                    AccountArtifactModel.account_id == "admin",
                    AccountArtifactModel.relative_path == "documents/cloud-bin-note.txt",
                ).one()
                bin_item = db.query(BinItemModel).where(BinItemModel.id == "bin-cloud-1").one()
                self.assertEqual(artifact.content, b"cloud plaintext")
                self.assertEqual(account_sync._ensure_aware(artifact.deleted_at), deleted_at)
                self.assertEqual(account_sync._ensure_aware(artifact.expires_at), expires_at)
                self.assertEqual(bin_item.name, "cloud-bin-note.txt")
        finally:
            local_engine.dispose()
            cloud_engine.dispose()

    def test_pull_bin_does_not_resurrect_newer_local_restore(self) -> None:
        local_engine = create_engine("sqlite://", future=True)
        cloud_engine = create_engine("sqlite://", future=True)
        try:
            Base.metadata.create_all(bind=local_engine)
            Base.metadata.create_all(bind=cloud_engine)
            LocalSession = sessionmaker(bind=local_engine, future=True)
            CloudSession = sessionmaker(bind=cloud_engine, future=True)

            deleted_at = datetime(2026, 6, 1, tzinfo=UTC)
            local_restored_at = datetime(2026, 6, 2, tzinfo=UTC)
            expires_at = deleted_at + timedelta(days=30)
            metadata_json = json.dumps({"relative_path": "documents/restored-note.txt"})
            with LocalSession() as db:
                db.add(AccountArtifactModel(
                    account_id="admin",
                    relative_path="documents/restored-note.txt",
                    content=b"restored",
                    sha256=account_sync._hash_bytes(b"restored"),
                    size_bytes=len(b"restored"),
                    updated_at=local_restored_at,
                    deleted_at=None,
                    expires_at=None,
                ))
                db.commit()
            with CloudSession() as db:
                db.add(BinItemModel(
                    id="bin-stale",
                    account_id="admin",
                    item_type="document",
                    item_id="doc-restored",
                    name="restored-note.txt",
                    metadata_json=metadata_json,
                    deleted_at=deleted_at,
                    expires_at=expires_at,
                ))
                db.commit()

            with mock.patch.object(account_sync, "_local_session", side_effect=lambda: LocalSession()), \
                mock.patch.object(account_sync, "_db_session", side_effect=lambda: CloudSession()):
                account_sync._pull_bin_items_from_cloud_sqlite("admin")

            with LocalSession() as db:
                self.assertIsNone(db.query(BinItemModel).where(BinItemModel.id == "bin-stale").first())
        finally:
            local_engine.dispose()
            cloud_engine.dispose()


class SqliteArtifactMetadataSkipTests(unittest.TestCase):
    def test_essentials_pull_defers_document_content_then_hydrates_later(self) -> None:
        local_engine = create_engine("sqlite://", future=True)
        cloud_engine = create_engine("sqlite://", future=True)
        try:
            AccountArtifactModel.__table__.create(bind=local_engine)
            AccountArtifactModel.__table__.create(bind=cloud_engine)
            LocalSession = sessionmaker(bind=local_engine, future=True)
            CloudSession = sessionmaker(bind=cloud_engine, future=True)

            dek = b"d" * 32
            updated_at = datetime(2026, 6, 2, tzinfo=UTC)
            doc_payload = b"document body"
            qa_payload = b"{\"curriculum\": {\"topics\": []}}"
            embeddings_payload = json.dumps({
                "chunks": [
                    {"chunk_id": "upload_note_txt_chunk_1", "text": "alpha", "vector": [0.1]},
                    {"chunk_id": "upload_note_txt_chunk_2", "text": "beta", "vector": [0.2]},
                ]
            }).encode("utf-8")
            with CloudSession() as db:
                db.add(AccountArtifactModel(
                    account_id="admin",
                    relative_path="documents/note.txt",
                    content=account_sync.encrypt_bytes(dek, doc_payload),
                    sha256=account_sync._hash_bytes(doc_payload),
                    size_bytes=len(doc_payload),
                    updated_at=updated_at,
                    hydration_status=account_sync.HYDRATION_FULL,
                ))
                db.add(AccountArtifactModel(
                    account_id="admin",
                    relative_path="documents/note.txt.embeddings.json",
                    content=account_sync.encrypt_bytes(dek, embeddings_payload),
                    sha256=account_sync._hash_bytes(embeddings_payload),
                    size_bytes=len(embeddings_payload),
                    updated_at=updated_at,
                    hydration_status=account_sync.HYDRATION_FULL,
                ))
                db.add(AccountArtifactModel(
                    account_id="admin",
                    relative_path="output/output_curriculum_qa.json",
                    content=account_sync.encrypt_bytes(dek, qa_payload),
                    sha256=account_sync._hash_bytes(qa_payload),
                    size_bytes=len(qa_payload),
                    updated_at=updated_at,
                    hydration_status=account_sync.HYDRATION_FULL,
                ))
                db.commit()

            report = account_sync.SyncReport(account_id="admin")
            with mock.patch.object(account_sync, "_local_session", side_effect=lambda: LocalSession()), \
                mock.patch.object(account_sync, "_db_session", side_effect=lambda: CloudSession()):
                downloaded = account_sync._pull_artifacts_from_cloud_sqlite(
                    "admin",
                    dek,
                    report,
                    content_mode="essentials",
                )

            self.assertEqual(downloaded, 2)
            with LocalSession() as db:
                doc = db.query(AccountArtifactModel).where(
                    AccountArtifactModel.relative_path == "documents/note.txt"
                ).one()
                embeddings = db.query(AccountArtifactModel).where(
                    AccountArtifactModel.relative_path == "documents/note.txt.embeddings.json"
                ).one()
                qa = db.query(AccountArtifactModel).where(
                    AccountArtifactModel.relative_path == "output/output_curriculum_qa.json"
                ).one()
                self.assertEqual(doc.content, b"")
                self.assertEqual(doc.hydration_status, account_sync.HYDRATION_METADATA_ONLY)
                self.assertEqual(embeddings.content, embeddings_payload)
                self.assertEqual(embeddings.hydration_status, account_sync.HYDRATION_FULL)
                self.assertEqual(qa.content, qa_payload)
                self.assertEqual(qa.hydration_status, account_sync.HYDRATION_FULL)

            with mock.patch.object(storage, "SessionLocal", LocalSession), \
                mock.patch.object(storage, "is_db_mode", return_value=True), \
                mock.patch.object(storage, "is_sqlite_mode", return_value=True), \
                mock.patch.object(storage, "_read_raw_text_docs", return_value=[]), \
                mock.patch.object(storage, "_read_uploaded_docs", return_value=[]):
                docs = storage.list_docs(account_id="admin", user_dek=dek, include_content=False)

            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0].chunk_count, 2)

            with mock.patch.object(account_sync, "_local_session", side_effect=lambda: LocalSession()), \
                mock.patch.object(account_sync, "_db_session", side_effect=lambda: CloudSession()), \
                mock.patch.object(account_sync, "is_sqlite_mode", return_value=True):
                result = account_sync.hydrate_account_artifacts("admin", dek)

            self.assertEqual(result["hydrated"], 1)
            self.assertEqual(result["remaining"], 0)
            with LocalSession() as db:
                doc = db.query(AccountArtifactModel).where(
                    AccountArtifactModel.relative_path == "documents/note.txt"
                ).one()
                self.assertEqual(doc.content, doc_payload)
                self.assertEqual(doc.hydration_status, account_sync.HYDRATION_FULL)
        finally:
            local_engine.dispose()
            cloud_engine.dispose()

    def test_push_skips_unchanged_artifact_without_encrypting_content(self) -> None:
        local_engine = create_engine("sqlite://", future=True)
        cloud_engine = create_engine("sqlite://", future=True)
        try:
            Base.metadata.create_all(bind=local_engine)
            Base.metadata.create_all(bind=cloud_engine)
            LocalSession = sessionmaker(bind=local_engine, future=True)
            CloudSession = sessionmaker(bind=cloud_engine, future=True)

            updated_at = datetime(2026, 6, 2, tzinfo=UTC)
            digest = account_sync._hash_bytes(b"same plaintext")
            with LocalSession() as db:
                db.add(AccountArtifactModel(
                    account_id="admin",
                    relative_path="documents/unchanged.txt",
                    content=b"same plaintext",
                    sha256=digest,
                    size_bytes=len(b"same plaintext"),
                    updated_at=updated_at,
                ))
                db.commit()
            with CloudSession() as db:
                db.add(AccountArtifactModel(
                    account_id="admin",
                    relative_path="documents/unchanged.txt",
                    content=b"existing encrypted payload",
                    sha256=digest,
                    size_bytes=len(b"same plaintext"),
                    updated_at=updated_at,
                ))
                db.commit()

            report = account_sync.SyncReport(account_id="admin")
            with mock.patch.object(account_sync, "_local_session", side_effect=lambda: LocalSession()), \
                mock.patch.object(account_sync, "_db_session", side_effect=lambda: CloudSession()), \
                mock.patch.object(account_sync, "encrypt_bytes", side_effect=AssertionError("encrypt should be skipped")):
                uploaded = account_sync._push_artifacts_to_cloud_sqlite("admin", b"d" * 32, False, report, updated_at)

            self.assertEqual(uploaded, 0)
            self.assertEqual(report.uploaded_files, 0)
            with CloudSession() as db:
                row = db.query(AccountArtifactModel).one()
                self.assertEqual(row.content, b"existing encrypted payload")
        finally:
            local_engine.dispose()
            cloud_engine.dispose()

    def test_pull_skips_unchanged_artifact_without_decrypting_content(self) -> None:
        local_engine = create_engine("sqlite://", future=True)
        cloud_engine = create_engine("sqlite://", future=True)
        try:
            Base.metadata.create_all(bind=local_engine)
            Base.metadata.create_all(bind=cloud_engine)
            LocalSession = sessionmaker(bind=local_engine, future=True)
            CloudSession = sessionmaker(bind=cloud_engine, future=True)

            updated_at = datetime(2026, 6, 2, tzinfo=UTC)
            digest = account_sync._hash_bytes(b"same plaintext")
            with LocalSession() as db:
                db.add(AccountArtifactModel(
                    account_id="admin",
                    relative_path="documents/unchanged.txt",
                    content=b"same plaintext",
                    sha256=digest,
                    size_bytes=len(b"same plaintext"),
                    updated_at=updated_at,
                ))
                db.commit()
            with CloudSession() as db:
                db.add(AccountArtifactModel(
                    account_id="admin",
                    relative_path="documents/unchanged.txt",
                    content=b"existing encrypted payload",
                    sha256=digest,
                    size_bytes=len(b"same plaintext"),
                    updated_at=updated_at,
                ))
                db.commit()

            report = account_sync.SyncReport(account_id="admin")
            with mock.patch.object(account_sync, "_local_session", side_effect=lambda: LocalSession()), \
                mock.patch.object(account_sync, "_db_session", side_effect=lambda: CloudSession()), \
                mock.patch.object(account_sync, "decrypt_bytes", side_effect=AssertionError("decrypt should be skipped")):
                downloaded = account_sync._pull_artifacts_from_cloud_sqlite("admin", b"d" * 32, report)

            self.assertEqual(downloaded, 0)
            self.assertEqual(report.downloaded_files, 0)
            with LocalSession() as db:
                row = db.query(AccountArtifactModel).one()
                self.assertEqual(row.content, b"same plaintext")
        finally:
            local_engine.dispose()
            cloud_engine.dispose()


if __name__ == "__main__":
    unittest.main()
