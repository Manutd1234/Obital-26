from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# Avoid hard dependency on optional runtime parser packages during unit tests.
sys.modules.setdefault("pypdf", types.SimpleNamespace(PdfReader=object))

from engine.pipeline.main import _build_qa_signature
from engine.pipeline.rag_engine import LightRAGVectorStore, RAGIngestionService, WebVectorStore


class PipelineBackendTests(unittest.TestCase):
    def test_qa_signature_uses_effective_backend(self) -> None:
        sig = _build_qa_signature(
            model="m",
            effective_settings={"qa_concurrency": 2, "qa_per_topic": 2, "max_global_context_chunks": 2, "max_topic_context_chunks": 2, "max_topics": 2},
            schema_retry_limit=3,
            backend_requested="lightrag",
            backend_effective="jsonl",
            retrieval_mode="hybrid",
            retrieval_top_k=8,
            retrieval_alpha=0.8,
        )
        self.assertEqual(sig["backend"], "jsonl")
        self.assertEqual(sig["backend_requested"], "lightrag")
        self.assertEqual(sig["backend_effective"], "jsonl")

    def test_lightrag_fallback_to_jsonl_when_unavailable(self) -> None:
        class _UnavailableLightRAG:
            name = "lightrag"

            def __init__(self, storage_dir: Path) -> None:
                self.available = False

        with tempfile.TemporaryDirectory() as td:
            storage = Path(td) / "graph"
            cache = Path(td) / "cache"
            with mock.patch("engine.pipeline.rag_engine.LightRAGVectorStore", _UnavailableLightRAG):
                svc = RAGIngestionService(
                    storage_dir=storage,
                    cache_dir=cache,
                    storage_backend="lightrag",
                    strict_backend=False,
                )
                self.assertEqual(svc.effective_backend, "jsonl")

    def test_lightrag_strict_raises_when_unavailable(self) -> None:
        class _UnavailableLightRAG:
            name = "lightrag"

            def __init__(self, storage_dir: Path) -> None:
                self.available = False
                self.unavailable_reason = "missing dependency"

        with tempfile.TemporaryDirectory() as td:
            storage = Path(td) / "graph"
            cache = Path(td) / "cache"
            with mock.patch("engine.pipeline.rag_engine.LightRAGVectorStore", _UnavailableLightRAG):
                with self.assertRaisesRegex(RuntimeError, "missing dependency"):
                    RAGIngestionService(
                        storage_dir=storage,
                        cache_dir=cache,
                        storage_backend="lightrag",
                        strict_backend=True,
                    )

    def test_web_backend_falls_back_to_jsonl_when_reserved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            storage = Path(td) / "graph"
            cache = Path(td) / "cache"
            svc = RAGIngestionService(
                storage_dir=storage,
                cache_dir=cache,
                storage_backend="web",
                web_backend_url="",
            )
            self.assertEqual(svc.effective_backend, "jsonl")

    def test_web_backend_strict_raises_while_reserved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            storage = Path(td) / "graph"
            cache = Path(td) / "cache"
            with self.assertRaisesRegex(RuntimeError, "reserved for a future hosted vector store"):
                RAGIngestionService(
                    storage_dir=storage,
                    cache_dir=cache,
                    storage_backend="web",
                    strict_backend=True,
                    web_backend_url="https://example.test",
                )

    def test_backend_switch_forces_reingest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            storage = Path(td) / "graph"
            cache = Path(td) / "cache"
            service = RAGIngestionService(storage_dir=storage, cache_dir=cache, storage_backend="jsonl")
            target = Path(td) / "doc.txt"
            target.write_text("hello world", encoding="utf-8")
            first = service.ingest(input_path=target, raw_text="hello world")
            self.assertFalse(first.skipped)

            # Simulate stale manifest from another backend.
            manifest_path = cache / "ingestion_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            key = str(target.resolve())
            manifest["files"][key]["backend"] = "web"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=True), encoding="utf-8")

            # Recreate service so it reloads manifest from disk.
            service2 = RAGIngestionService(storage_dir=storage, cache_dir=cache, storage_backend="jsonl")
            second = service2.ingest(input_path=target, raw_text="hello world")
            self.assertFalse(second.skipped)

    def test_lightrag_has_file_rows_requires_backend_doc(self) -> None:
        class _FakeLightRAG:
            def __init__(self, working_dir: str) -> None:
                self.docs: dict[str, dict] = {}

            def insert_custom_chunks(self, *, full_text: str, text_chunks: list[str], doc_id: str) -> None:
                self.docs[doc_id] = {"content": full_text, "chunks": text_chunks}

            async def aget_docs_by_ids(self, doc_ids: list[str]) -> dict[str, dict]:
                return {doc_id: self.docs[doc_id] for doc_id in doc_ids if doc_id in self.docs}

            async def adelete_by_doc_id(self, doc_id: str) -> None:
                self.docs.pop(doc_id, None)

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(sys.modules, {"lightrag": types.SimpleNamespace(LightRAG=_FakeLightRAG)}):
                store = LightRAGVectorStore(Path(td))
                self.assertTrue(store.available)
                store.replace_file_rows(file_key="doc", rows=[{"text": "alpha"}, {"text": "beta"}])
                chunk_hashes = [
                    hashlib.sha256("alpha".encode("utf-8")).hexdigest(),
                    hashlib.sha256("beta".encode("utf-8")).hexdigest(),
                ]
                self.assertTrue(
                    store.has_file_rows(
                        file_key="doc",
                        expected_chunk_hashes=chunk_hashes,
                    )
                )

                store._client.docs.clear()
                self.assertFalse(
                    store.has_file_rows(
                        file_key="doc",
                        expected_chunk_hashes=chunk_hashes,
                    )
                )
                store.replace_file_rows(file_key="doc", rows=[])
                self.assertTrue(store.has_file_rows(file_key="doc", expected_chunk_hashes=[]))

    def test_empty_file_clears_previous_jsonl_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            storage = Path(td) / "graph"
            cache = Path(td) / "cache"
            service = RAGIngestionService(storage_dir=storage, cache_dir=cache, storage_backend="jsonl")
            target = Path(td) / "doc.txt"
            target.write_text("hello world", encoding="utf-8")
            service.ingest(input_path=target, raw_text="hello world")
            self.assertEqual(len(service.load_rows_for_qa()), 1)

            result = service.ingest(input_path=target, raw_text="")
            self.assertEqual(result.reason, "empty_text")
            self.assertEqual(service.load_rows_for_qa(), [])

    def test_incomplete_jsonl_rows_force_reingest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            storage = Path(td) / "graph"
            cache = Path(td) / "cache"
            service = RAGIngestionService(
                storage_dir=storage,
                cache_dir=cache,
                storage_backend="jsonl",
                chunk_size=100,
                chunk_overlap=0,
            )
            target = Path(td) / "doc.txt"
            text = "a" * 260
            service.ingest(input_path=target, raw_text=text)

            rows = service.load_rows_for_qa()
            self.assertGreater(len(rows), 1)
            index_path = storage / "vector_index.jsonl"
            index_path.write_text(json.dumps(rows[0], ensure_ascii=True) + "\n", encoding="utf-8")

            service2 = RAGIngestionService(
                storage_dir=storage,
                cache_dir=cache,
                storage_backend="jsonl",
                chunk_size=100,
                chunk_overlap=0,
            )
            result = service2.ingest(input_path=target, raw_text=text)
            self.assertFalse(result.skipped)
            self.assertEqual(result.reason, "updated")

    def test_web_vector_store_is_reserved_placeholder(self) -> None:
        store = WebVectorStore(base_url="https://example.test", api_key="", timeout_sec=5)
        self.assertFalse(store.available)
        with self.assertRaises(NotImplementedError):
            store.load_all_rows()


if __name__ == "__main__":
    unittest.main()
