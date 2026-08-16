from __future__ import annotations

import hashlib
import json
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

from engine.pipeline.constants import (
    DEFAULT_BACKEND,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_WORKERS,
)


@dataclass(frozen=True)
class IngestionResult:
    input_path: Path
    skipped: bool
    reason: str
    chunks: int
    vectors_written: int
    backend_used: str
    embedding_fallback_used: bool = False


class RAGIngestionService:
    """Step 3 ingestion with cache-aware updates and parallel embedding workers."""

    def __init__(
        self,
        storage_dir: Path,
        cache_dir: Path,
        *,
        workers: int = DEFAULT_WORKERS,
        # Tune this to control chunk granularity. Larger chunk -> fewer vectors, broader context.
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        # Tune this to preserve context between neighboring chunks.
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        # Change this to test different local embedding models.
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        # `lightrag` uses LightRAG when available; otherwise auto-falls back to `jsonl`.
        storage_backend: str = DEFAULT_BACKEND,
        # When true, fail if requested backend cannot be initialized.
        strict_backend: bool = False,
        # Used only by `web` backend adapter.
        web_backend_url: str | None = None,
        web_backend_api_key: str | None = None,
        web_backend_timeout_sec: float = 20.0,
    ) -> None:
        self.storage_dir = storage_dir.resolve()
        self.cache_dir = cache_dir.resolve()
        self.workers = max(1, workers)
        self.chunk_size = max(100, chunk_size)
        self.chunk_overlap = max(0, min(chunk_overlap, self.chunk_size - 1))
        self.embedding_model = embedding_model
        self.requested_backend = storage_backend.lower().strip()
        self.strict_backend = strict_backend
        self.web_backend_url = (web_backend_url or "").strip()
        self.web_backend_api_key = (web_backend_api_key or "").strip()
        self.web_backend_timeout_sec = max(1.0, float(web_backend_timeout_sec))

        self._embedding_fallback_used = False
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.manifest_path = self.cache_dir / "ingestion_manifest.json"
        self.vector_index_path = self.storage_dir / "vector_index.jsonl"
        self._manifest = self._load_manifest()
        self._embed_fn = self._build_embedder()
        self._store = self._build_store_adapter()
        self.effective_backend = self._store.name

    def ingest(
        self,
        *,
        input_path: Path,
        raw_text: str,
        force_reingest: bool = False,
        purge_missing: bool = False,
    ) -> IngestionResult:
        resolved_input = input_path.resolve()
        if purge_missing:
            self._purge_missing_entries()

        content_hash = _sha256(raw_text.encode("utf-8"))
        key = str(resolved_input)
        current = self._manifest["files"].get(key)
        chunks = _chunk_text(raw_text, self.chunk_size, self.chunk_overlap)
        chunk_hashes = [_sha256(chunk.encode("utf-8")) for chunk in chunks]
        if (
            not force_reingest
            and current
            and current.get("sha256") == content_hash
            and current.get("backend") == self.effective_backend
            and current.get("chunk_hashes") == chunk_hashes
            and current.get("embedding_model") == self.embedding_model
            and current.get("chunk_size") == self.chunk_size
            and current.get("chunk_overlap") == self.chunk_overlap
            and self._store.has_file_rows(file_key=key, expected_chunk_hashes=chunk_hashes)
        ):
            return IngestionResult(
                input_path=resolved_input,
                skipped=True,
                reason="unchanged_hash",
                chunks=0,
                vectors_written=0,
                backend_used=self._store.name,
            )

        if not chunks:
            self._store.replace_file_rows(file_key=key, rows=[])
            self._manifest["files"][key] = {
                "sha256": content_hash,
                "last_ingested_at": _utc_now_iso(),
                "chunk_count": 0,
                "chunk_hashes": [],
                "backend": self.effective_backend,
                "embedding_model": self.embedding_model,
                "chunk_size": self.chunk_size,
                "chunk_overlap": self.chunk_overlap,
            }
            self._save_manifest()
            return IngestionResult(
                input_path=resolved_input,
                skipped=False,
                reason="empty_text",
                chunks=0,
                vectors_written=0,
                backend_used=self._store.name,
            )

        vectors = self._embed_chunks_parallel(chunks)
        rows: list[dict[str, Any]] = []
        for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
            rows.append(
                {
                    "file_path": key,
                    "file_sha256": content_hash,
                    "chunk_id": idx,
                    "chunk_sha256": chunk_hashes[idx],
                    "embedding_model": self.embedding_model,
                    "embedding_dim": len(vector),
                    "text": chunk,
                    "vector": vector,
                    "tags": [],
                    "updated_at": _utc_now_iso(),
                }
            )

        self._store.replace_file_rows(file_key=key, rows=rows)

        self._manifest["files"][key] = {
            "sha256": content_hash,
            "last_ingested_at": _utc_now_iso(),
            "chunk_count": len(chunks),
            "chunk_hashes": chunk_hashes,
            "backend": self.effective_backend,
            "embedding_model": self.embedding_model,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
        }
        self._save_manifest()
        return IngestionResult(
            input_path=resolved_input,
            skipped=False,
            reason="updated",
            chunks=len(chunks),
            vectors_written=len(vectors),
            backend_used=self._store.name,
            embedding_fallback_used=self._embedding_fallback_used,
        )

    def _build_embedder(self):
        from engine.shared.embedding_loader import build_embed_fn

        embed_fn, _dim, used_fallback = build_embed_fn(self.embedding_model)
        self._embedding_fallback_used = used_fallback
        return embed_fn

    def _embed_chunks_parallel(self, chunks: list[str]) -> list[list[float]]:
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            vectors = list(ex.map(self._embed_fn, chunks))
        return vectors

    def _build_store_adapter(self):
        if self.requested_backend == "jsonl":
            return JsonlVectorStore(self.vector_index_path)
        if self.requested_backend == "lightrag":
            adapter = LightRAGVectorStore(self.storage_dir)
            if adapter.available:
                return adapter
            if self.strict_backend:
                reason = getattr(adapter, "unavailable_reason", "").strip()
                suffix = f": {reason}" if reason else "."
                raise RuntimeError(f"Requested backend 'lightrag' is not available in this environment{suffix}")
            return JsonlVectorStore(self.vector_index_path)
        if self.requested_backend == "web":
            adapter = WebVectorStore(
                base_url=self.web_backend_url,
                api_key=self.web_backend_api_key,
                timeout_sec=self.web_backend_timeout_sec,
            )
            if adapter.available:
                return adapter
            if self.strict_backend:
                reason = getattr(adapter, "unavailable_reason", "").strip()
                suffix = f": {reason}" if reason else "."
                raise RuntimeError(f"Requested backend 'web' is not available in this environment{suffix}")
            return JsonlVectorStore(self.vector_index_path)
        raise RuntimeError(f"Unsupported backend '{self.requested_backend}'.")

    def load_rows_for_qa(self) -> list[dict[str, Any]]:
        return self._store.load_all_rows()

    def _load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"version": 1, "files": {}}
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def _save_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(self._manifest, ensure_ascii=True, indent=2), encoding="utf-8"
        )

    def _purge_missing_entries(self) -> None:
        keys = list(self._manifest["files"].keys())
        for path_str in keys:
            if not Path(path_str).exists():
                del self._manifest["files"][path_str]
        self._save_manifest()


def _chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if not text.strip():
        return []
    chunks: list[str] = []
    start = 0
    step = max(1, chunk_size - chunk_overlap)
    snap_tolerance = min(50, chunk_size // 4)
    while start < len(text):
        raw_end = start + chunk_size
        end = raw_end
        if raw_end < len(text):
            # Snap forward to next whitespace to avoid cutting mid-word.
            snap = text.find(" ", raw_end, raw_end + snap_tolerance)
            if snap > start:
                end = snap
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - chunk_overlap if end < len(text) else len(text)
    return chunks


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VectorStore(Protocol):
    name: str
    available: bool

    def replace_file_rows(self, *, file_key: str, rows: list[dict[str, Any]]) -> None: ...
    def load_all_rows(self) -> list[dict[str, Any]]: ...
    def has_file_rows(self, *, file_key: str, expected_chunk_hashes: list[str]) -> bool: ...


class JsonlVectorStore:
    """Simple local store. Keeps one JSONL row per text chunk + vector."""

    name = "jsonl"
    available = True

    def __init__(self, path: Path) -> None:
        self.path = path

    def replace_file_rows(self, *, file_key: str, rows: list[dict[str, Any]]) -> None:
        kept_lines: list[str] = []
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("file_path") != file_key:
                        kept_lines.append(line.rstrip("\n"))
        with self.path.open("w", encoding="utf-8") as f:
            for line in kept_lines:
                f.write(line + "\n")
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=True) + "\n")

    def load_all_rows(self) -> list[dict[str, Any]]:
        return _read_jsonl_rows(self.path)

    def has_file_rows(self, *, file_key: str, expected_chunk_hashes: list[str]) -> bool:
        rows = [row for row in self.load_all_rows() if row.get("file_path") == file_key]
        return _rows_match_chunk_hashes(rows=rows, expected_chunk_hashes=expected_chunk_hashes)


class LightRAGVectorStore:
    """
    LightRAG adapter hook.
    - If LightRAG import/init fails, `available` is False and caller should fallback.
    - Replace logic here with your exact LightRAG APIs if they differ by version.
    """

    name = "lightrag"

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.available = False
        self._doc_map_path = storage_dir / "lightrag_doc_map.json"
        self._client = None
        self._doc_map: dict[str, dict[str, Any]] = {}
        self.unavailable_reason = ""
        try:
            # Version-sensitive import: keep guarded.
            from lightrag import LightRAG  # type: ignore

            self._client = LightRAG(working_dir=str(storage_dir))
            self._doc_map = self._load_doc_map()
            self.available = True
        except Exception as exc:
            self.unavailable_reason = str(exc) or exc.__class__.__name__
            self.available = False

    def replace_file_rows(self, *, file_key: str, rows: list[dict[str, Any]]) -> None:
        if not self.available:
            raise RuntimeError("LightRAG backend not available.")
        existing_entry = self._doc_map.get(file_key, {})
        existing_doc_id = str(existing_entry.get("doc_id", "")).strip()
        if not rows:
            if existing_doc_id:
                try:
                    _run_sync(self._client.adelete_by_doc_id(existing_doc_id))
                except Exception:
                    pass
            self._doc_map.pop(file_key, None)
            self._save_doc_map()
            return

        new_doc_id = _sha256(f"{file_key}:{_utc_now_iso()}".encode("utf-8"))
        text_chunks = [str(row.get("text", "")).strip() for row in rows if str(row.get("text", "")).strip()]
        full_text = "\n\n".join(text_chunks)
        self._client.insert_custom_chunks(
            full_text=full_text,
            text_chunks=text_chunks,
            doc_id=new_doc_id,
        )

        # Commit map update only after successful insert so replace is atomic.
        self._doc_map[file_key] = {
            "doc_id": new_doc_id,
            "chunks": text_chunks,
            "chunk_hashes": [
                str(row.get("chunk_sha256", "")).strip()
                or _sha256(str(row.get("text", "")).strip().encode("utf-8"))
                for row in rows
                if str(row.get("text", "")).strip()
            ],
        }
        self._save_doc_map()

        # Best effort cleanup of old document after new one is committed.
        if existing_doc_id:
            try:
                _run_sync(self._client.adelete_by_doc_id(existing_doc_id))
            except Exception:
                pass

    def load_all_rows(self) -> list[dict[str, Any]]:
        if not self.available:
            raise RuntimeError("LightRAG backend not available.")
        if not self._doc_map:
            return []
        doc_ids = [
            str(entry.get("doc_id", "")).strip()
            for entry in self._doc_map.values()
            if isinstance(entry, dict) and str(entry.get("doc_id", "")).strip()
        ]
        if not doc_ids:
            return []
        by_doc = _run_sync(self._client.aget_docs_by_ids(doc_ids))
        reverse_map = {
            str(entry.get("doc_id", "")).strip(): file_key
            for file_key, entry in self._doc_map.items()
            if isinstance(entry, dict)
        }
        rows: list[dict[str, Any]] = []
        for doc_id, status in by_doc.items():
            file_key = reverse_map.get(doc_id, "")
            if not file_key:
                continue
            entry = self._doc_map.get(file_key, {})
            stored_chunks = entry.get("chunks", []) if isinstance(entry, dict) else []
            stored_hashes = entry.get("chunk_hashes", []) if isinstance(entry, dict) else []
            # LightRAG exposes chunk IDs in chunks_list; recover text from our committed chunk snapshot.
            chunks = stored_chunks if isinstance(stored_chunks, list) else []
            chunk_hashes = stored_hashes if isinstance(stored_hashes, list) else []
            for idx, chunk in enumerate(chunks):
                text = str(chunk).strip()
                if not text:
                    continue
                chunk_hash = str(chunk_hashes[idx]).strip() if idx < len(chunk_hashes) else _sha256(text.encode("utf-8"))
                rows.append(
                    {
                        "file_path": file_key,
                        "chunk_id": idx,
                        "chunk_sha256": chunk_hash,
                        "text": text,
                    }
                )
        return rows

    def has_file_rows(self, *, file_key: str, expected_chunk_hashes: list[str]) -> bool:
        entry = self._doc_map.get(file_key)
        if not expected_chunk_hashes:
            return entry is None
        if not isinstance(entry, dict) or not entry.get("chunks"):
            return False
        stored_hashes = entry.get("chunk_hashes", [])
        if list(stored_hashes) != expected_chunk_hashes:
            return False
        doc_id = str(entry.get("doc_id", "")).strip()
        if not doc_id:
            return False
        try:
            by_doc = _run_sync(self._client.aget_docs_by_ids([doc_id]))
        except Exception:
            return False
        return isinstance(by_doc, dict) and bool(by_doc.get(doc_id))

    def _load_doc_map(self) -> dict[str, dict[str, Any]]:
        if not self._doc_map_path.exists():
            return {}
        try:
            payload = json.loads(self._doc_map_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for k, v in payload.items():
            if not isinstance(k, str):
                continue
            if isinstance(v, str):
                # Backward compatibility with old format: {file_key: doc_id}
                out[k] = {"doc_id": v, "chunks": []}
                continue
            if isinstance(v, dict):
                doc_id = str(v.get("doc_id", "")).strip()
                chunks_raw = v.get("chunks", [])
                chunks = [str(c) for c in chunks_raw if isinstance(c, str)]
                hashes_raw = v.get("chunk_hashes", [])
                chunk_hashes = [str(h) for h in hashes_raw if isinstance(h, str)]
                if len(chunk_hashes) != len(chunks):
                    chunk_hashes = [_sha256(c.encode("utf-8")) for c in chunks]
                if doc_id:
                    out[k] = {"doc_id": doc_id, "chunks": chunks, "chunk_hashes": chunk_hashes}
        return out

    def _save_doc_map(self) -> None:
        self._doc_map_path.write_text(
            json.dumps(self._doc_map, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )


class WebVectorStore:
    """
    Reserved adapter for a future hosted vector store.

    The pipeline keeps this backend name and config shape so callers can switch
    storage later, but there is intentionally no HTTP contract yet.
    """

    name = "web"
    available = False

    def __init__(self, *, base_url: str, api_key: str | None, timeout_sec: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout_sec = timeout_sec
        self.unavailable_reason = "web backend is reserved for a future hosted vector store."

    def replace_file_rows(self, *, file_key: str, rows: list[dict[str, Any]]) -> None:
        raise NotImplementedError(self.unavailable_reason)

    def load_all_rows(self) -> list[dict[str, Any]]:
        raise NotImplementedError(self.unavailable_reason)

    def has_file_rows(self, *, file_key: str, expected_chunk_hashes: list[str]) -> bool:
        raise NotImplementedError(self.unavailable_reason)


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _run_sync(awaitable):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("Async LightRAG operation cannot run inside an active event loop.")


def _rows_match_chunk_hashes(*, rows: list[dict[str, Any]], expected_chunk_hashes: list[str]) -> bool:
    if len(rows) != len(expected_chunk_hashes):
        return False
    by_chunk_id: dict[int, str] = {}
    for row in rows:
        try:
            chunk_id = int(row.get("chunk_id"))
        except (TypeError, ValueError):
            return False
        if chunk_id in by_chunk_id:
            return False
        chunk_hash = str(row.get("chunk_sha256", "")).strip()
        text = str(row.get("text", "")).strip()
        if not chunk_hash and text:
            chunk_hash = _sha256(text.encode("utf-8"))
        if not chunk_hash:
            return False
        by_chunk_id[chunk_id] = chunk_hash
    actual = [by_chunk_id.get(idx, "") for idx in range(len(expected_chunk_hashes))]
    return actual == expected_chunk_hashes
