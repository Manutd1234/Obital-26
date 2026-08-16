from __future__ import annotations

from pathlib import Path


# Add/remove supported document types here. Folder mode and extraction share this.
SUPPORTED_INPUT_EXTENSIONS = {".pdf", ".txt", ".md"}

# Default folders/files. Change these if you want a different project layout.
DEFAULT_DOCUMENTS_DIR = Path("engine/documents")
DEFAULT_RAW_TEXT_OUTPUT = Path("engine/output/raw_text.txt")
DEFAULT_QA_OUTPUT = Path("engine/output/output_curriculum_qa.json")
DEFAULT_CACHE_DIR = Path("engine/cache")

# Ingestion defaults. Larger chunks reduce requests/index size but may blur concepts.
DEFAULT_WORKERS = 4
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
DEFAULT_BACKEND = "jsonl"
DEFAULT_RETRIEVAL_MODE = "hybrid"
DEFAULT_RETRIEVAL_TOP_K = 8
DEFAULT_RETRIEVAL_ALPHA = 0.8

# QA defaults. These are used unless --cheap-mode or explicit CLI flags override them.
NORMAL_QA_DEFAULTS = {
    "qa_concurrency": 6,
    "qa_per_topic": 5,
    "max_global_context_chunks": 24,
    "max_topic_context_chunks": 10,
    "max_topics": 12,
}

# Low-cost preset for token-saving test runs.
CHEAP_QA_DEFAULTS = {
    "qa_concurrency": 2,
    "qa_per_topic": 2,
    "max_global_context_chunks": 6,
    "max_topic_context_chunks": 3,
    "max_topics": 4,
}
