"""Server-side embedding service.

Provides text embedding via sentence-transformers with a deterministic
hash-based fallback only when explicitly enabled for local development.
"""

from __future__ import annotations

import hashlib
import logging
import os
from threading import Lock

logger = logging.getLogger(__name__)

_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
_EMBEDDING_MODE = os.getenv("EMBEDDING_MODE", "client").strip().lower()
_ALLOW_HASH_FALLBACK = os.getenv("EMBEDDING_ALLOW_HASH_FALLBACK", "").strip().lower() in {"1", "true", "yes"}
_EMBEDDING_DIM: int | None = None

_model = None
_model_lock = Lock()
_model_failed = False


class EmbeddingUnavailableError(RuntimeError):
    """Raised when semantic embeddings are not configured in this process."""


def server_embeddings_enabled() -> bool:
    return _EMBEDDING_MODE == "server"


def _detect_embedding_dimension(model) -> int:
    for method_name in ("get_sentence_embedding_dimension", "get_embedding_dimension"):
        method = getattr(model, method_name, None)
        if callable(method):
            dim = method()
            if isinstance(dim, int) and dim > 0:
                return dim
    return 384


def _load_model():
    global _model, _model_failed, _EMBEDDING_DIM
    if not server_embeddings_enabled():
        _model_failed = True
        _EMBEDDING_DIM = 384
        return
    if _model is not None or _model_failed:
        return
    with _model_lock:
        if _model is not None or _model_failed:
            return
        try:
            from engine.shared.embedding_loader import get_embedding_model
            _model = get_embedding_model(_MODEL_NAME)
        except Exception as exc:
            _model_failed = True
            _EMBEDDING_DIM = 384
            if _ALLOW_HASH_FALLBACK:
                logger.warning(
                    "Embedding model unavailable (%s). Using hash-based fallback (dim=%d).",
                    exc,
                    _EMBEDDING_DIM,
                )
            else:
                logger.warning("Embedding model unavailable (%s). Server embeddings disabled.", exc)
            return
        try:
            _EMBEDDING_DIM = _detect_embedding_dimension(_model)
            logger.info("Loaded embedding model: %s (dim=%d)", _MODEL_NAME, _EMBEDDING_DIM)
        except Exception as exc:
            _EMBEDDING_DIM = 384
            logger.warning(
                "Could not detect embedding dimension for loaded model %s (%s). Using default dim=%d.",
                _MODEL_NAME, exc, _EMBEDDING_DIM,
            )


def _fallback_embed(text: str, dim: int | None = None) -> list[float]:
    """Deterministic hash-based fallback when sentence-transformers is unavailable."""
    if dim is None:
        dim = _EMBEDDING_DIM or 384
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    while len(values) < dim:
        digest = hashlib.sha256(digest).digest()
        for b in digest:
            values.append((b / 255.0) * 2.0 - 1.0)
            if len(values) == dim:
                break
    return values


def embed_text(text: str) -> list[float]:
    """Embed a single text string. Returns a list of floats."""
    if not server_embeddings_enabled() and not _ALLOW_HASH_FALLBACK:
        raise EmbeddingUnavailableError("server embeddings are disabled; provide a client query_vector")
    _load_model()
    if _model is not None:
        try:
            vector = _model.encode(text, normalize_embeddings=True)
            return [float(v) for v in vector.tolist()]
        except Exception as exc:
            if _ALLOW_HASH_FALLBACK:
                logger.warning("Embedding failed, using fallback: %s", exc)
                return _fallback_embed(text)
            logger.warning("Embedding failed and fallback is disabled: %s", exc)
            raise EmbeddingUnavailableError("server embedding failed") from exc
    if _ALLOW_HASH_FALLBACK:
        return _fallback_embed(text)
    raise EmbeddingUnavailableError("server embeddings are unavailable")


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts. Returns a list of float lists."""
    if not server_embeddings_enabled() and not _ALLOW_HASH_FALLBACK:
        raise EmbeddingUnavailableError("server embeddings are disabled; provide client vectors")
    _load_model()
    if _model is not None:
        try:
            vectors = _model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
            return [[float(v) for v in vec.tolist()] for vec in vectors]
        except Exception as exc:
            if _ALLOW_HASH_FALLBACK:
                logger.warning("Batch embedding failed, using fallback: %s", exc)
                return [_fallback_embed(t) for t in texts]
            logger.warning("Batch embedding failed and fallback is disabled: %s", exc)
            raise EmbeddingUnavailableError("server batch embedding failed") from exc
    if _ALLOW_HASH_FALLBACK:
        return [_fallback_embed(t) for t in texts]
    raise EmbeddingUnavailableError("server embeddings are unavailable")


def embedding_dim() -> int:
    if not server_embeddings_enabled():
        return 384
    _load_model()
    return _EMBEDDING_DIM or 384


def is_model_loaded() -> bool:
    _load_model()
    return _model is not None
