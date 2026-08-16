from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.services import storage
from backend.app.schemas import Lesson


class PipelineConnectionTests(unittest.TestCase):
    def test_list_lessons_reads_pipeline_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            output_dir = base / "engine" / "accounts" / "admin" / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            qa_path = output_dir / "output_curriculum_qa.json"
            qa_path.write_text(
                json.dumps(
                    {
                        "curriculum": {
                            "topics": [
                                {"id": "topic_01", "title": "Topic One", "summary": "Summary one"},
                                {"id": "topic_02", "title": "Topic Two", "summary": "Summary two"},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(storage, "BASE_DIR", base):
                lessons = storage.list_lessons("admin")

            self.assertEqual(len(lessons), 2)
            self.assertEqual(lessons[0].id, "topic_01")
            self.assertEqual(lessons[0].title, "Topic One")

    def test_list_docs_includes_raw_text_and_uploaded_docs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw_text_dir = base / "engine" / "accounts" / "admin" / "output" / "raw_text"
            docs_dir = base / "engine" / "accounts" / "admin" / "documents"
            raw_text_dir.mkdir(parents=True, exist_ok=True)
            docs_dir.mkdir(parents=True, exist_ok=True)

            (raw_text_dir / "A.txt").write_text("raw line 1\nraw line 2", encoding="utf-8")
            (docs_dir / "B.txt").write_text("upload line 1\nupload line 2", encoding="utf-8")

            with mock.patch.object(storage, "BASE_DIR", base), mock.patch.object(storage, "_is_db_storage_mode", return_value=False):
                docs = storage.list_docs("admin")

            names = sorted(doc.name for doc in docs)
            self.assertIn("A.txt", names)
            self.assertIn("B.txt", names)

    def test_list_docs_does_not_drop_same_stem_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw_text_dir = base / "engine" / "accounts" / "admin" / "output" / "raw_text"
            docs_dir = base / "engine" / "accounts" / "admin" / "documents"
            raw_text_dir.mkdir(parents=True, exist_ok=True)
            docs_dir.mkdir(parents=True, exist_ok=True)

            (raw_text_dir / "Same.txt").write_text("raw", encoding="utf-8")
            (docs_dir / "Same.txt").write_text("upload", encoding="utf-8")

            with mock.patch.object(storage, "BASE_DIR", base), mock.patch.object(storage, "_is_db_storage_mode", return_value=False):
                docs = storage.list_docs("admin")

            self.assertEqual(len(docs), 2)
            self.assertEqual(len({doc.id for doc in docs}), 2)

    def test_list_docs_logs_extraction_failures(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            docs_dir = base / "engine" / "accounts" / "admin" / "documents"
            docs_dir.mkdir(parents=True, exist_ok=True)
            (docs_dir / "bad.xyz").write_text("bad", encoding="utf-8")

            with mock.patch.object(storage, "BASE_DIR", base), mock.patch.object(storage, "_is_db_storage_mode", return_value=False):
                with self.assertLogs("backend.app.services.storage", level="WARNING") as logs:
                    docs = storage.list_docs("admin")

            self.assertEqual(docs, [])
            self.assertTrue(any("Skipping unreadable document" in line for line in logs.output))

    def test_add_doc_writes_to_account_documents(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            docs_dir = base / "engine" / "accounts" / "admin" / "documents"

            with mock.patch.object(storage, "BASE_DIR", base), mock.patch.object(storage, "_is_db_storage_mode", return_value=False):
                doc = storage.add_doc("Note.txt", b"hello\nworld", "admin")

            self.assertTrue((docs_dir / "Note.txt").exists())
            self.assertEqual(doc.name, "Note.txt")
            self.assertGreaterEqual(len(doc.chunks), 1)

    def test_get_lesson_supports_natural_language_lookup(self) -> None:
        lessons = [
            Lesson(id="lesson_trees", title="Trees", objective="", content_blocks=[]),
            Lesson(id="lesson_geometry", title="Geometry", objective="", content_blocks=[]),
        ]
        with mock.patch.object(storage, "list_account_lessons", return_value=lessons):
            with mock.patch.object(storage, "list_lessons", return_value=[]):
                found = storage.get_lesson("lesson on tree", "admin")

        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.id, "lesson_trees")


if __name__ == "__main__":
    unittest.main()
