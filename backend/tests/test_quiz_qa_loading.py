from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.app.services.db
import backend.app.services.models
import backend.app.api.quiz

from backend.app.services.models import AccountArtifactModel
from backend.app.api.quiz import _load_qa, _load_qa_from_db, _load_qa_from_disk


_QA_PAYLOAD = {
    "curriculum": {
        "title": "Test Curriculum",
        "description": "A test curriculum.",
        "topics": [
            {"id": "t1", "title": "Trees", "summary": "Binary trees and B-trees."},
        ],
    },
    "qa_sets": [
        {
            "topic_id": "t1",
            "topic_title": "Trees",
            "questions": [
                {
                    "question": "What is a B-tree?",
                    "answer": "A self-balancing search tree.",
                    "difficulty": "medium",
                    "source_context": None,
                },
            ],
        },
    ],
    "metadata": None,
}


class QuizQADiskLoadingTests(unittest.TestCase):
    def test_load_qa_from_disk_success(self) -> None:
        with patch("backend.app.services.db.is_stateless_db_mode", return_value=False):
            with patch("backend.app.api.quiz._load_qa_from_disk") as mock_disk:
                from engine.pipeline.models import CurriculumQAOutput

                mock_disk.return_value = CurriculumQAOutput.model_validate(_QA_PAYLOAD)
                qa = _load_qa("testuser")
                self.assertEqual(qa.curriculum.title, "Test Curriculum")
                self.assertEqual(len(qa.qa_sets), 1)

    def test_load_qa_from_disk_missing_raises_404(self) -> None:
        from fastapi import HTTPException

        with patch("backend.app.services.db.is_stateless_db_mode", return_value=False):
            with patch("backend.app.api.quiz._load_qa_from_disk", return_value=None):
                with self.assertRaises(HTTPException) as ctx:
                    _load_qa("testuser")
                self.assertEqual(ctx.exception.status_code, 404)
                self.assertIn("pipeline", ctx.exception.detail)


class QuizQADbLoadingTests(unittest.TestCase):
    memory_engine = None
    SessionLocal = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.orig_db_url = backend.app.services.db.DATABASE_URL
        cls.orig_engine = backend.app.services.db.engine
        cls.orig_session = backend.app.services.db.SessionLocal

        cls.memory_engine = create_engine("sqlite:///:memory:", future=True)
        cls.SessionLocal = sessionmaker(bind=cls.memory_engine, autoflush=False, autocommit=False, future=True)
        backend.app.services.db.Base.metadata.create_all(bind=cls.memory_engine)

        backend.app.services.db.DATABASE_URL = "postgresql+psycopg://dummy@localhost/dummy"
        backend.app.services.db.engine = cls.memory_engine
        backend.app.services.db.SessionLocal = cls.SessionLocal

    @classmethod
    def tearDownClass(cls) -> None:
        backend.app.services.db.DATABASE_URL = cls.orig_db_url
        backend.app.services.db.engine = cls.orig_engine
        backend.app.services.db.SessionLocal = cls.orig_session

    def setUp(self) -> None:
        with self.SessionLocal() as db:
            db.query(AccountArtifactModel).delete()
            db.commit()

    def _store_qa_artifact(self, account_id: str, payload: dict | None = None, *, encrypted: bool = False) -> None:
        data = json.dumps(payload or _QA_PAYLOAD, ensure_ascii=True).encode("utf-8")
        if encrypted:
            from backend.app.services.crypto_local import encrypt_bytes

            dek = b"k" * 32
            data = encrypt_bytes(dek, data)
        else:
            dek = None
        with self.SessionLocal() as db:
            db.add(AccountArtifactModel(
                account_id=account_id,
                relative_path="output/output_curriculum_qa.json",
                content=data,
                sha256="abc123",  # _load_qa_from_db does not verify hashes
                size_bytes=len(data),
            ))
            db.commit()

    def test_load_qa_from_db_success(self) -> None:
        self._store_qa_artifact("user1")
        qa = _load_qa_from_db("user1", user_dek=None)
        self.assertIsNotNone(qa)
        self.assertEqual(qa.curriculum.title, "Test Curriculum")
        self.assertEqual(qa.qa_sets[0].topic_title, "Trees")

    def test_load_qa_from_db_missing_returns_none(self) -> None:
        qa = _load_qa_from_db("nonexistent", user_dek=None)
        self.assertIsNone(qa)

    def test_load_qa_from_db_with_decryption(self) -> None:
        self._store_qa_artifact("user2", encrypted=True)
        dek = b"k" * 32
        qa = _load_qa_from_db("user2", user_dek=dek)
        self.assertIsNotNone(qa)
        self.assertEqual(qa.curriculum.title, "Test Curriculum")

    def test_load_qa_from_db_wrong_key_raises_400(self) -> None:
        from fastapi import HTTPException

        self._store_qa_artifact("user2b", encrypted=True)
        wrong_key = b"z" * 32
        with self.assertRaises(HTTPException) as ctx:
            _load_qa_from_db("user2b", user_dek=wrong_key)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("decrypt", ctx.exception.detail.lower())

    def test_load_qa_from_db_invalid_json_returns_none(self) -> None:
        with self.SessionLocal() as db:
            db.add(AccountArtifactModel(
                account_id="user3",
                relative_path="output/output_curriculum_qa.json",
                content=b"not valid json",
                sha256="def456",  # _load_qa_from_db does not verify hashes
                size_bytes=13,
            ))
            db.commit()
        qa = _load_qa_from_db("user3", user_dek=None)
        self.assertIsNone(qa)

    def test_load_qa_stateless_db_mode_delegates_to_db(self) -> None:
        from engine.pipeline.models import CurriculumQAOutput

        self._store_qa_artifact("user4")
        with patch("backend.app.services.db.is_stateless_db_mode", return_value=True):
            qa = _load_qa("user4")
            self.assertEqual(qa.curriculum.title, "Test Curriculum")

    def test_load_qa_stateless_db_mode_missing_raises_404(self) -> None:
        from fastapi import HTTPException

        with patch("backend.app.services.db.is_stateless_db_mode", return_value=True):
            with self.assertRaises(HTTPException) as ctx:
                _load_qa("nonexistent")
            self.assertEqual(ctx.exception.status_code, 404)
            self.assertIn("sync", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
