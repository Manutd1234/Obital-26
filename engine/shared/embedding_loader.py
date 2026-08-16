from __future__ import annotations

import functools
import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"


def _resolve_cache_folder() -> str:
    env = os.environ.get("OBITAL_MODEL_CACHE_DIR", "").strip()
    if env:
        return env
    return str(
        Path(__file__).resolve().parent.parent.parent / "data" / "models" / "sentence_transformers"
    )


def _load_model_uncached(model_name: str, cache_folder: str, local_files_only: bool):
    from sentence_transformers import SentenceTransformer  # type: ignore

    model = SentenceTransformer(
        model_name,
        cache_folder=cache_folder,
        local_files_only=local_files_only,
    )
    logger.info(
        "Loaded embedding model: %s (dim=%d)", model_name, model.get_embedding_dimension()
    )
    return model


@functools.lru_cache(maxsize=1)
def get_embedding_model(model_name: str = DEFAULT_MODEL_NAME):
    """Return a process-wide cached SentenceTransformer instance."""
    cache_folder = _resolve_cache_folder()
    model_dir = Path(cache_folder) / f"models--sentence-transformers--{model_name}"
    local_only = model_dir.exists()
    return _load_model_uncached(model_name, cache_folder, local_only)


def build_embed_fn(
    model_name: str = DEFAULT_MODEL_NAME,
) -> tuple[callable, int, bool]:
    """Return (embed_fn, embedding_dim, used_fallback) using the shared cached loader.

    Falls back to a deterministic hash-based embedder if the model is
    unavailable. The returned ``embed_fn`` signature is
    ``embed_fn(text: str) -> list[float]``.
    """
    try:
        model = get_embedding_model(model_name)
        dim = model.get_embedding_dimension()

        def _embed(text: str) -> list[float]:
            vector = model.encode(text, normalize_embeddings=True)
            return [float(v) for v in vector.tolist()]

        return _embed, dim, False
    except Exception as exc:
        logger.warning(
            "SentenceTransformer unavailable (%s). Using hash-based fallback "
            "(dim=96). Semantic similarity will be degraded.",
            exc,
        )
        return fallback_embed, 96, True


def fallback_embed(text: str, dim: int = 96) -> list[float]:
    """Deterministic hash-based fallback when sentence-transformers is unavailable."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    while len(values) < dim:
        digest = hashlib.sha256(digest).digest()
        for b in digest:
            values.append((b / 255.0) * 2.0 - 1.0)
            if len(values) == dim:
                break
    return values
