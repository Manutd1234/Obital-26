from __future__ import annotations

import argparse
import os
from pathlib import Path

MODEL_NAME = "all-MiniLM-L6-v2"
MODEL_CACHE_DIR = Path(
    os.environ.get(
        "OBITAL_MODEL_CACHE_DIR",
        str(Path(__file__).resolve().parent.parent.parent / "data" / "models" / "sentence_transformers"),
    )
)


def is_embedding_model_ready() -> bool:
    model_dir = MODEL_CACHE_DIR / "models--sentence-transformers--all-MiniLM-L6-v2"
    return model_dir.exists()


def ensure_embedding_model() -> Path:
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    from sentence_transformers import SentenceTransformer  # type: ignore

    SentenceTransformer(
        MODEL_NAME,
        cache_folder=str(MODEL_CACHE_DIR),
        local_files_only=is_embedding_model_ready(),
    )
    return MODEL_CACHE_DIR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download/cache local embedding model.")
    return parser


def run() -> None:
    _ = build_parser().parse_args()
    cache_dir = ensure_embedding_model()
    print(f"Embedding model '{MODEL_NAME}' is ready at: {cache_dir}")


if __name__ == "__main__":
    run()
