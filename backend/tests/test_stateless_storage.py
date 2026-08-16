from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import original modules cleanly
import backend.app.services.db
import backend.app.services.models
import backend.app.services.storage
import backend.app.services.settings_service
import engine.quiz.account_system

from backend.app.services.storage import (
    _is_db_storage_mode,
    list_docs,
    add_doc,
    get_doc,
    search_docs,
    delete_doc,
)
from backend.app.services.settings_service import (
    get_effective_system_prompt,
    update_settings,
)
from backend.app.schemas import SettingsPatchRequest, ChatSettingsPatch
from backend.app.api.common import active_user_dek
from engine.quiz.account_system import AccountStore


class StatelessStorageTests(unittest.TestCase):
    SessionLocal = None
    memory_engine = None

    @classmethod
    def setUpClass(cls) -> None:
        # 1. Save original attributes to prevent test suite pollution
        cls.orig_db_url = backend.app.services.db.DATABASE_URL
        cls.orig_db_engine = backend.app.services.db.engine
        cls.orig_db_session = backend.app.services.db.SessionLocal
        cls.orig_storage_session = backend.app.services.storage.SessionLocal
        cls.orig_effective_url = backend.app.services.db.EFFECTIVE_DATABASE_URL

        # 2. Create the in-memory engine and session
        cls.memory_engine = create_engine("sqlite:///:memory:", future=True)
        cls.SessionLocal = sessionmaker(bind=cls.memory_engine, autoflush=False, autocommit=False, future=True)

        # 3. Create all tables in the memory database
        backend.app.services.db.Base.metadata.create_all(bind=cls.memory_engine)

        # 4. Patch targets dynamically
        backend.app.services.db.DATABASE_URL = "postgresql+psycopg://dummy:dummy@localhost/dummy"
        backend.app.services.db.EFFECTIVE_DATABASE_URL = backend.app.services.db.DATABASE_URL
        backend.app.services.db.engine = cls.memory_engine
        backend.app.services.db.SessionLocal = cls.SessionLocal
        backend.app.services.storage.SessionLocal = cls.SessionLocal

    @classmethod
    def tearDownClass(cls) -> None:
        # 5. Restore original attributes completely
        backend.app.services.db.DATABASE_URL = cls.orig_db_url
        backend.app.services.db.EFFECTIVE_DATABASE_URL = cls.orig_effective_url
        backend.app.services.db.engine = cls.orig_db_engine
        backend.app.services.db.SessionLocal = cls.orig_db_session
        backend.app.services.storage.SessionLocal = cls.orig_storage_session

    def setUp(self) -> None:
        # Clear any inserted records to keep tests isolated
        with self.SessionLocal() as db:
            db.query(backend.app.services.models.BinItemModel).delete()
            db.query(backend.app.services.models.AccountArtifactModel).delete()
            db.query(backend.app.services.models.AccountRegistryRecordModel).delete()
            db.commit()
        backend.app.services.storage._doc_cache.clear()

    def test_is_db_storage_mode(self) -> None:
        self.assertTrue(_is_db_storage_mode())

    def test_stateless_settings_prompt(self) -> None:
        user_dek = b"k" * 32

        # Verify that get_effective_system_prompt returns default if not exists
        prompt = get_effective_system_prompt("user_123", user_dek=user_dek)
        self.assertIn("patient and rigorous tutor", prompt)

        # Update the system prompt
        new_prompt = "You are a friendly and funny AI tutor."
        patch = SettingsPatchRequest(chat=ChatSettingsPatch(system_prompt=new_prompt))
        update_settings("user_123", patch, user_dek=user_dek)

        # Verify that get_effective_system_prompt returns the updated prompt
        updated = get_effective_system_prompt("user_123", user_dek=user_dek)
        self.assertEqual(updated, new_prompt)

        # Verify that it is written to the database under "config/chat_system_prompt.md"
        from backend.app.services.models import AccountArtifactModel
        with self.SessionLocal() as db:
            row = db.query(AccountArtifactModel).where(
                AccountArtifactModel.account_id == "user_123",
                AccountArtifactModel.relative_path == "config/chat_system_prompt.md"
            ).first()
            self.assertIsNotNone(row)

            settings_row = db.query(AccountArtifactModel).where(
                AccountArtifactModel.account_id == "user_123",
                AccountArtifactModel.relative_path == "config/settings.json"
            ).first()
            self.assertIsNotNone(settings_row)

    def test_stateless_account_store(self) -> None:
        # Use a temp file so the store is isolated between test runs
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store_path = Path(f.name)
        try:
            store = AccountStore(store_path)

            # Create user
            username = "studious_student"
            pin = "1234"
            profile = store.create_user(username=username, pin=pin)
            self.assertEqual(profile["username"], username)

            # Check user exists
            self.assertTrue(store.user_exists(username))

            # Verify profile is persisted in the JSON store
            self.assertIn("pin_hash", profile)

            # Verify login
            logged_in = store.verify_login(username=username, pin=pin)
            self.assertIsNotNone(logged_in)
            self.assertEqual(logged_in["username"], username)

            # Verify duplicate creation raises
            with self.assertRaises(RuntimeError):
                store.create_user(username=username, pin="5678")
        finally:
            store_path.unlink(missing_ok=True)

    def test_user_exists_does_not_load_or_decrypt_target_profile(self) -> None:
        from backend.app.services.models import AccountRegistryRecordModel

        with self.SessionLocal() as db:
            db.add(
                AccountRegistryRecordModel(
                    account_id="friend_account",
                    profile_json='{"friends":"enc:v1:not-readable-with-callers-key"}',
                )
            )
            db.commit()

        store = AccountStore(Path("unused-in-db-mode.json"))
        with mock.patch.object(
            store,
            "_load_user",
            side_effect=AssertionError("existence checks must not load private profiles"),
        ):
            self.assertTrue(store.user_exists("friend_account"))
            self.assertFalse(store.user_exists("missing_account"))

    def test_account_store_preserves_encrypted_progress_on_wrong_dek(self) -> None:
        from backend.app.services.crypto_local import encrypt_text
        from backend.app.services.models import AccountRegistryRecordModel

        good_dek = b"g" * 32
        wrong_dek = b"w" * 32
        lesson_progress = {
            "active_lesson_id": "lesson_1",
            "lessons": {
                "lesson_1": {
                    "lesson_id": "lesson_1",
                    "title": "Private Lesson",
                    "topic": "secrets",
                    "checkpoints": [],
                }
            },
        }
        encrypted_progress = encrypt_text(good_dek, json.dumps(lesson_progress))
        encrypted_history = encrypt_text(good_dek, json.dumps([{"quiz_id": "quiz_1"}]))
        profile = {
            "username": "admin",
            "pin_hash": "legacy",
            "quiz_history": encrypted_history,
            "lesson_progress": encrypted_progress,
        }

        with self.SessionLocal() as db:
            db.add(
                AccountRegistryRecordModel(
                    account_id="admin",
                    profile_json=json.dumps(profile),
                )
            )
            db.commit()

        token = active_user_dek.set(wrong_dek)
        try:
            store = AccountStore(Path("unused.json"))
            from engine.quiz.account_system import AccountDataKeyMismatch
            with self.assertRaises(AccountDataKeyMismatch):
                store.get_lesson_progress(username="admin")
        finally:
            active_user_dek.reset(token)

        with self.SessionLocal() as db:
            row = db.query(AccountRegistryRecordModel).where(
                AccountRegistryRecordModel.account_id == "admin"
            ).first()
            self.assertIsNotNone(row)
            saved = json.loads(row.profile_json)

        self.assertEqual(saved["lesson_progress"], encrypted_progress)
        self.assertEqual(saved["quiz_history"], encrypted_history)

    def test_stateless_document_upload_and_retrieval(self) -> None:
        user_dek = b"d" * 32
        doc_content = b"This is a private student document with sensitive study details."
        doc_name = "chemistry_notes.txt"

        # Add document with DEK
        doc = add_doc(name=doc_name, content=doc_content, account_id="user_abc", user_dek=user_dek)
        self.assertEqual(doc.name, doc_name)

        # Verify add_doc raises ValueError if user_dek is None
        with self.assertRaises(ValueError):
            add_doc(name="test.txt", content=b"hello", account_id="user_abc", user_dek=None)

        # Verify that it is written to the database encrypted (original content is not in the row)
        from backend.app.services.models import AccountArtifactModel
        with self.SessionLocal() as db:
            row = db.query(AccountArtifactModel).where(
                AccountArtifactModel.account_id == "user_abc",
                AccountArtifactModel.relative_path == f"documents/{doc_name}"
            ).first()
            self.assertIsNotNone(row)
            self.assertNotEqual(row.content, doc_content)

        # Retrieve documents listing with correct DEK
        docs = list_docs(account_id="user_abc", user_dek=user_dek)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].name, doc_name)
        self.assertGreater(len(docs[0].chunks), 0)

        # Metadata-only listing must not decrypt/extract chunks; this keeps /docs cheap in serverless mode.
        metadata_docs = list_docs(account_id="user_abc", user_dek=user_dek, include_content=False)
        self.assertEqual(len(metadata_docs), 1)
        self.assertEqual(metadata_docs[0].name, doc_name)
        self.assertEqual(metadata_docs[0].chunks, [])

        # Retrieve documents listing with incorrect or no DEK (metadata only, no content)
        docs_no_dek = list_docs(account_id="user_abc", user_dek=None, include_content=True)
        self.assertEqual(len(docs_no_dek), 1)
        self.assertEqual(docs_no_dek[0].name, doc_name)
        self.assertEqual(docs_no_dek[0].chunks, [])

        # Get document by ID with correct DEK
        retrieved_doc = get_doc(doc.id, account_id="user_abc", user_dek=user_dek)
        self.assertIsNotNone(retrieved_doc)
        self.assertEqual(retrieved_doc.name, doc_name)

        # Search documents with correct DEK
        search_results = search_docs("chemistry", account_id="user_abc", user_dek=user_dek)
        self.assertEqual(len(search_results), 1)

    def test_delete_doc_invalidates_cached_document(self) -> None:
        user_dek = b"d" * 32
        doc = add_doc(
            name="cached_delete.txt",
            content=b"This document should disappear after delete.",
            account_id="delete_user",
            user_dek=user_dek,
        )

        self.assertIsNotNone(get_doc(doc.id, account_id="delete_user", user_dek=user_dek))

        self.assertTrue(delete_doc(doc.id, account_id="delete_user", user_dek=user_dek))
        self.assertIsNone(get_doc(doc.id, account_id="delete_user", user_dek=user_dek))

    def test_filesystem_delete_doc_without_db_session_moves_to_trash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            with mock.patch.object(backend.app.services.storage, "BASE_DIR", base_dir), \
                mock.patch.object(backend.app.services.storage, "_is_db_storage_mode", return_value=False), \
                mock.patch.object(backend.app.services.storage, "SessionLocal", None):
                doc = add_doc(
                    name="local_delete.txt",
                    content=b"Local no database delete path.",
                    account_id="local_user",
                )

                self.assertTrue(delete_doc(doc.id, account_id="local_user"))
                self.assertIsNone(get_doc(doc.id, account_id="local_user"))

                trash_file = (
                    base_dir
                    / "engine"
                    / "accounts"
                    / "local_user"
                    / "documents"
                    / ".trash"
                    / "local_delete.txt"
                )
                self.assertTrue(trash_file.exists())

    def test_sync_config_plain_text_compatibility(self) -> None:
        from backend.app.services.models import AccountArtifactModel
        from backend.app.services.settings_service import get_effective_system_prompt
        import hashlib
        
        # Insert a plain-text prompt artifact (simulating a push of a config file)
        prompt_text = "You are a quantum physics tutor."
        with self.SessionLocal() as db:
            db.add(
                AccountArtifactModel(
                    account_id="user_sync_test",
                    relative_path="config/chat_system_prompt.md",
                    content=prompt_text.encode("utf-8"),
                    sha256=hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
                    size_bytes=len(prompt_text),
                )
            )
            db.commit()

        # Verify that get_effective_system_prompt retrieves and decodes the prompt successfully as plain text
        retrieved = get_effective_system_prompt("user_sync_test", user_dek=b"d" * 32)
        self.assertEqual(retrieved, prompt_text)

    def test_encrypted_prompt_with_wrong_dek_falls_back_to_default(self) -> None:
        from backend.app.services.crypto_local import encrypt_bytes
        from backend.app.services.models import AccountArtifactModel
        from backend.app.services.settings_service import DEFAULT_SYSTEM_PROMPT

        prompt_bytes = b"You are an encrypted tutor.\n"
        correct_dek = b"c" * 32
        wrong_dek = b"w" * 32
        with self.SessionLocal() as db:
            db.add(
                AccountArtifactModel(
                    account_id="user_wrong_dek",
                    relative_path="config/chat_system_prompt.md",
                    content=encrypt_bytes(correct_dek, prompt_bytes),
                    sha256="dummy_sha",
                    size_bytes=len(prompt_bytes),
                )
            )
            db.commit()

        retrieved = get_effective_system_prompt("user_wrong_dek", user_dek=wrong_dek)
        self.assertEqual(retrieved, DEFAULT_SYSTEM_PROMPT)

    def test_chat_service_document_context_lookup_with_dek(self) -> None:
        from backend.app.services.chat_service import send_chat_message
        from backend.app.schemas import SendMessageRequest
        from backend.app.services.storage import create_session
        from unittest.mock import patch
        
        user_dek = b"d" * 32
        doc_content = b"Water boils at 100 degrees Celsius."
        doc_name = "science_notes.txt"
        
        # 1. Add document
        doc = add_doc(name=doc_name, content=doc_content, account_id="chat_user", user_dek=user_dek)
        
        # 2. Create session
        session = create_session(account_id="chat_user")
        
        # 3. Create a SendMessageRequest with the chunk ID of our document
        chunk_id = doc.chunks[0].chunk_id
        req = SendMessageRequest(
            message="What temperature does water boil at?",
            lesson_id=None,
            doc_chunk_ids=[chunk_id]
        )
        
        # 4. Verify that send_chat_message retrieves the doc text correctly
        with patch("backend.app.services.chat_service.generate_reply") as mock_gen:
            mock_gen.return_value = "Mocked reply"
            send_chat_message(
                session_id=session.id,
                payload=req,
                account_id="chat_user",
                user_dek=user_dek
            )
            mock_gen.assert_called_once()
            called_args, called_kwargs = mock_gen.call_args
            self.assertIn("Water boils at 100 degrees Celsius.", called_kwargs["doc_texts"])


if __name__ == "__main__":
    unittest.main()
